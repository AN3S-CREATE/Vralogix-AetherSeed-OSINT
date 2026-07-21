"""The full investigation pipeline.

Orchestrates one :class:`~aetherseed.schemas.SubjectSeed` into one
:class:`~aetherseed.schemas.InvestigationRun`:

1. Register the run, open its hash-chained audit log, create the graph.
2. Expand the subject with AetherMind (queries, handles, related entities,
   money hypotheses, candidate seeds).
3. Crawl reachable seed URLs (priority frontier, per-item isolation), extracting
   entities/relationships and folding them into the graph. Optional screenshots.
4. Optional enrichment (DNS/WHOIS/registry) over discovered entities.
5. Follow-the-money analysis; gap analysis.
6. Optional auto-seeding under safety budgets + approval gate.
7. Persist everything and return the structured result.

Resumability: on start, previously-fetched pages for the run seed the crawler's
dedup sets, and a checkpoint watermark records progress after every page.
Fault isolation: any per-page failure is captured in ``failed_items`` (the DLQ)
and never aborts the run; a configurable failure-rate threshold triggers a
graceful ``partial``/``failed`` finish.
"""

from __future__ import annotations

import re
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from aetherseed.config import Settings, get_settings
from aetherseed.core.acquisition.browser import PlaywrightFetcher
from aetherseed.core.acquisition.crawler import Crawler
from aetherseed.core.acquisition.extract import HtmlExtractor
from aetherseed.core.acquisition.fetcher import HttpxFetcher
from aetherseed.core.acquisition.search import SearchProvider, get_search_provider
from aetherseed.core.ai.engine import AetherMind
from aetherseed.core.enrichment.enrichers import get_enrichers
from aetherseed.core.graph.money import FollowTheMoney
from aetherseed.core.graph.resolution import canonical_key
from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.core.interfaces import Enricher
from aetherseed.core.rag.index import get_vector_index
from aetherseed.core.seeding.budget import SafetyBudget
from aetherseed.core.seeding.engine import SeedingEngine
from aetherseed.core.storage.asset_store import FilesystemAssetStore
from aetherseed.core.storage.audit import AuditLog
from aetherseed.core.storage.db import get_sessionmaker, init_db
from aetherseed.core.storage.models import PageRecord
from aetherseed.core.storage.repositories import (
    AssetRepository,
    CheckpointRepository,
    FailedItemRepository,
    PageRepository,
    RunRepository,
    SeedRepository,
)
from aetherseed.errors import AetherError, ErrorCategory, classify_exception
from aetherseed.logging import get_logger, log_context
from aetherseed.schemas import (
    AssetManifest,
    Entity,
    EntityType,
    EvidenceSnippet,
    GraphDelta,
    InvestigationRun,
    Lead,
    Provenance,
    Relationship,
    RunStatus,
    SeedStatus,
    StructuredError,
    SubjectSeed,
)

log = get_logger(__name__)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$", re.IGNORECASE)

ProgressCb = Callable[[str, dict[str, Any]], None]


def _looks_crawlable(identifier: str) -> str | None:
    """Return a fetchable URL for ``identifier`` if it looks like one, else None."""
    ident = identifier.strip()
    if _URL_RE.match(ident):
        return ident
    if " " not in ident and _DOMAIN_RE.match(ident):
        return f"https://{ident}"
    return None


class InvestigationPipeline:
    """Runs a complete investigation for one subject."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        ai: AetherMind | None = None,
        progress: ProgressCb | None = None,
        fetcher_factory: Callable[..., Any] | None = None,
        search_provider: SearchProvider | None = None,
        vector_index_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        init_db(self.settings)
        self.ai = ai or AetherMind(settings=self.settings)
        self._progress = progress or (lambda _e, _d: None)
        self._Session = get_sessionmaker(self.settings)
        # Injectable so tests (and alternative backends) can supply a fetcher
        # without going over the network. Default: the static httpx fetcher.
        self._fetcher_factory = fetcher_factory or (
            lambda respect_robots: HttpxFetcher(self.settings, respect_robots=respect_robots)
        )
        self._search_provider = search_provider or get_search_provider(self.settings)
        # Per-run corpus index (RAG). Default degrades to an offline in-memory
        # lexical index; a factory is injectable for tests / alternative backends.
        self._vector_index_factory = vector_index_factory or (
            lambda: get_vector_index(self.settings)
        )

    async def run(
        self,
        subject: SubjectSeed,
        *,
        run_id: str | None = None,
        auto_seed: bool = False,
        take_screenshots: bool = False,
        enrich: bool = False,
        render: bool = False,
        search: bool = False,
    ) -> InvestigationRun:
        """Execute the full investigation and return the structured result."""
        result = InvestigationRun(subject=subject)
        if run_id:
            result.run_id = run_id
        graph = NetworkXGraphStore(graph_id=subject.existing_graph_id)
        audit = AuditLog(result.run_id, self.settings)
        result.audit_log_ref = audit.ref
        result.status = RunStatus.RUNNING

        session = self._Session()
        try:
            with log_context(run_id=result.run_id):
                RunRepository(session).create(result)
                SeedRepository(session).add_from_subject(
                    result.run_id, subject, origin="manual", status=SeedStatus.APPROVED
                )
                session.commit()
                audit.emit("run.started", subject=subject.model_dump(mode="json"))
                self._progress("run.started", {"run_id": result.run_id})

                await self._execute(subject, result, graph, audit, session,
                                    auto_seed=auto_seed, take_screenshots=take_screenshots,
                                    enrich=enrich, render=render, search=search)
                session.commit()
        except Exception as exc:
            log.error("run.fatal", error=str(exc), tb=traceback.format_exc())
            result.status = RunStatus.FAILED
            result.errors.append(
                StructuredError(
                    error=type(exc).__name__,
                    message=str(exc),
                    category=classify_exception(exc),
                    retryable=False,
                )
            )
            audit.emit("run.failed", error=str(exc))
            session.rollback()
        finally:
            result.metrics.finished_at = datetime.now(UTC)
            result.metrics.llm_calls = self.ai.llm_calls
            try:
                RunRepository(session).save_result(result)
                session.commit()
            except Exception:
                session.rollback()
            session.close()

        return result

    # ------------------------------------------------------------------ core

    async def _execute(
        self,
        subject: SubjectSeed,
        result: InvestigationRun,
        graph: NetworkXGraphStore,
        audit: AuditLog,
        session: Any,
        *,
        auto_seed: bool,
        take_screenshots: bool,
        enrich: bool,
        render: bool,
        search: bool,
    ) -> None:
        constraints = subject.constraints

        # --- 1. Seed expansion -------------------------------------------------
        expansion = await self.ai.expand_seed(subject)
        audit.emit(
            "ai.expansion",
            backend=self.ai.backend_name,
            queries=len(expansion.search_queries),
            candidates=len(expansion.candidate_seeds),
        )
        self._progress("ai.expansion", {"queries": len(expansion.search_queries)})

        # --- 2. Determine crawlable seed URLs ---------------------------------
        seed_urls: list[str] = []
        for ident in subject.primary_identifiers:
            if (url := _looks_crawlable(ident)) is not None:
                seed_urls.append(url)
        for cand in expansion.candidate_seeds:
            for ident in cand.identifiers:
                if (url := _looks_crawlable(ident)) is not None:
                    seed_urls.append(url)
        seed_urls = list(dict.fromkeys(seed_urls))

        # --- 2b. Search-driven discovery (opt-in): let a bare name seed a crawl.
        if search:
            seed_urls = await self._discover_via_search(subject, expansion, seed_urls, audit)

        run_entities: dict[str, Entity] = {}
        run_relationships: list[Relationship] = []
        # Per-run RAG corpus: page texts collected during the crawl, later used
        # to ground leads with retrieved evidence snippets.
        corpus = self._vector_index_factory()

        # --- 3. Crawl ---------------------------------------------------------
        if seed_urls and constraints.max_pages > 0:
            await self._crawl(
                subject, result, graph, audit, session, seed_urls,
                run_entities, run_relationships, corpus, render=render,
                take_screenshots=take_screenshots,
            )
        else:
            audit.emit("crawl.skipped", reason="no crawlable seed URLs")
            self._progress("crawl.skipped", {})

        # --- 4. Enrichment (optional) -----------------------------------------
        if enrich:
            await self._enrich(graph, audit, run_entities, run_relationships)

        # --- 5. Follow-the-money ---------------------------------------------
        money = FollowTheMoney(graph).analyze()
        if money.red_flags:
            audit.emit("money.red_flags", count=len(money.red_flags))

        # --- 6. Build & score leads ------------------------------------------
        leads = self._build_leads(subject, expansion, graph, money, run_entities)
        leads = await self.ai.score_leads(subject, leads)
        result.new_leads = sorted(leads, key=lambda x: x.score, reverse=True)
        self._attach_evidence(result.new_leads, corpus, audit)

        # --- 7. Gap analysis --------------------------------------------------
        gap = await self.ai.analyze_gaps(
            subject,
            list(run_entities.values()),
            coverage_note=f"{result.metrics.pages_fetched} pages, {len(run_entities)} entities",
        )
        result.gap_report = gap
        result.next_recommended_actions = gap.recommended_actions
        audit.emit("ai.gap_analysis", coverage=gap.coverage_score, missing=len(gap.missing_dimensions))

        # --- 8. Auto-seeding (optional) --------------------------------------
        if auto_seed:
            budget = SafetyBudget(
                self.settings,
                budget_usd=constraints.budget_usd,
                require_approval=constraints.require_approval,
            )
            outcome = SeedingEngine(budget).propose(
                result.run_id, subject, list(run_entities.values()), session, audit,
                expansion=expansion, gap=gap, max_new=constraints.max_seeds,
            )
            result.metrics.seeds_generated = outcome.total_created
            session.commit()
            self._progress("seeding.done", {"created": outcome.total_created})

        # --- 9. Persist graph delta + finalise -------------------------------
        result.graph_delta = GraphDelta(
            nodes=list(run_entities.values()), edges=run_relationships
        )
        self._persist_graph(result.graph_delta, audit)
        result.asset_manifest = self._collect_assets(session, result.run_id)
        result.metrics.pending = self._count_pending_seeds(session, result.run_id)
        self._finalize_status(result)
        audit.emit(
            "run.finished",
            status=result.status.value,
            graph=graph.stats(),
            metrics=result.metrics.model_dump(mode="json"),
        )
        self._progress("run.finished", {"status": result.status.value})

    async def _discover_via_search(
        self,
        subject: SubjectSeed,
        expansion: Any,
        seed_urls: list[str],
        audit: AuditLog,
        *,
        max_queries: int = 6,
    ) -> list[str]:
        """Discover crawlable URLs by searching identifiers + top expansion queries.

        Result URLs are added to the seed set (deduped, capped). They remain
        subject to the crawler's SSRF/robots/rate controls when fetched.
        """
        provider = self._search_provider
        if provider is None or provider.name == "none":
            audit.emit("search.skipped", reason="no search backend configured")
            return seed_urls

        queries = list(dict.fromkeys([*subject.primary_identifiers, *expansion.search_queries]))
        cap = min(subject.constraints.max_pages or 20, 20)
        discovered: list[str] = []
        for query in queries[:max_queries]:
            for res in await provider.search(query, max_results=self.settings.search_max_results):
                if res.url.startswith(("http://", "https://")):
                    discovered.append(res.url)
            if len(discovered) >= cap:
                break

        merged = list(dict.fromkeys([*seed_urls, *discovered]))[: max(cap, len(seed_urls))]
        audit.emit(
            "search.performed",
            provider=provider.name,
            queries=min(len(queries), max_queries),
            discovered=len(set(discovered)),
        )
        self._progress("search.performed", {"discovered": len(set(discovered))})
        return merged

    async def _crawl(
        self,
        subject: SubjectSeed,
        result: InvestigationRun,
        graph: NetworkXGraphStore,
        audit: AuditLog,
        session: Any,
        seed_urls: list[str],
        run_entities: dict[str, Entity],
        run_relationships: list[Relationship],
        corpus: Any,
        *,
        render: bool,
        take_screenshots: bool,
    ) -> None:
        constraints = subject.constraints
        seen_urls, seen_hashes = self._load_seen(session, result.run_id)

        fetcher = self._fetcher_factory(respect_robots=constraints.respect_robots)
        extractor = HtmlExtractor()
        crawler = Crawler(
            fetcher,
            extractor,
            max_depth=constraints.max_depth,
            max_pages=constraints.max_pages,
            workers=self.settings.acq_max_concurrency,
            render=render,
            seen_urls=seen_urls,
            seen_hashes=seen_hashes,
        )
        screenshotter = PlaywrightFetcher(self.settings) if take_screenshots else None
        page_repo = PageRepository(session)
        fail_repo = FailedItemRepository(session)
        ckpt = CheckpointRepository(session)
        shots_taken = 0

        try:
            async for outcome in crawler.crawl(seed_urls):
                if outcome.skipped:
                    result.metrics.skipped += 1
                    audit.emit("page.skipped", url=outcome.url, reason=outcome.error)
                    ckpt.save(result.run_id, "crawl", {"processed": result.metrics.processed})
                    session.commit()
                    continue
                result.metrics.processed += 1
                if not outcome.ok:
                    result.metrics.failed += 1
                    fail_repo.record(
                        result.run_id, kind="fetch", target=outcome.url,
                        category="transient", retryable=True,
                        error=outcome.error or "unknown",
                    )
                    audit.emit("page.failed", url=outcome.url, error=outcome.error)
                else:
                    result.metrics.succeeded += 1
                    result.metrics.pages_fetched += 1
                    assert outcome.result is not None and outcome.content is not None
                    result.metrics.bytes_downloaded += len(outcome.result.content)
                    page_repo.record(
                        result.run_id, url=outcome.url, final_url=outcome.result.final_url,
                        status_code=outcome.result.status_code,
                        content_type=outcome.result.content_type,
                        content_hash=outcome.result.content_hash,
                        title=outcome.content.title, seed_id=None,
                        depth=outcome.depth, rendered=outcome.result.rendered,
                    )
                    await self._ingest_page(outcome, graph, run_entities, run_relationships)
                    self._index_page(corpus, outcome)
                    audit.emit(
                        "page.fetched", url=outcome.url,
                        entities=len(outcome.content.entities), depth=outcome.depth,
                    )
                    if screenshotter is not None and shots_taken < 25:
                        shots_taken += await self._maybe_screenshot(
                            screenshotter, outcome.url, session, result
                        )

                ckpt.save(result.run_id, "crawl", {"processed": result.metrics.processed})
                session.commit()
                self._progress("page", {"url": outcome.url, "ok": outcome.ok})

                # Fail-fast guard.
                if (
                    result.metrics.processed >= 10
                    and result.metrics.failure_rate > constraints.fail_fast_error_pct
                ):
                    audit.emit("run.fail_fast", failure_rate=result.metrics.failure_rate)
                    result.errors.append(
                        StructuredError(
                            error="FailFastThreshold",
                            message=f"failure rate {result.metrics.failure_rate:.0%} exceeded threshold",
                            category=ErrorCategory.TRANSIENT,
                            retryable=True,
                        )
                    )
                    break
        finally:
            await fetcher.aclose()
            if screenshotter is not None:
                await screenshotter.aclose()

    async def _ingest_page(
        self,
        outcome: Any,
        graph: NetworkXGraphStore,
        run_entities: dict[str, Entity],
        run_relationships: list[Relationship],
    ) -> None:
        entities, relationships = await self.ai.extract(outcome.content)
        for ent in entities:
            run_entities.setdefault(canonical_key(ent), ent)
        run_relationships.extend(relationships)
        graph.apply_delta(GraphDelta(nodes=entities, edges=relationships))

    @staticmethod
    def _index_page(corpus: Any, outcome: Any) -> None:
        """Add a fetched page's text to the run's RAG corpus (best-effort)."""
        text = (outcome.content.text or "").strip()
        if not text:
            return
        try:
            corpus.add(
                [outcome.url],
                [text],
                [{"url": outcome.result.final_url, "title": outcome.content.title or ""}],
            )
        except Exception as exc:  # indexing must never abort a run
            log.debug("rag.index_failed", url=outcome.url, error=str(exc))

    def _attach_evidence(self, leads: list[Lead], corpus: Any, audit: AuditLog) -> None:
        """Ground each lead with top retrieved snippets from the corpus.

        Non-fatal: any retrieval failure is logged and leaves the lead unchanged,
        preserving the run. No-op when RAG is disabled or the corpus is empty.
        """
        k = self.settings.rag_snippets_per_lead
        if not self.settings.rag_enabled or k <= 0 or len(corpus) == 0:
            return
        attached = 0
        for lead in leads:
            query = " ".join(p for p in (lead.title, lead.value, lead.summary) if p).strip()
            if not query:
                continue
            try:
                hits = corpus.query(query, k=k)
            except Exception as exc:
                log.debug("rag.query_failed", lead=lead.id, error=str(exc))
                continue
            snippets = [
                EvidenceSnippet(
                    text=h["snippet"], source_url=h.get("metadata", {}).get("url"), score=h["score"]
                )
                for h in hits
                if h.get("score", 0.0) >= self.settings.rag_min_score
            ]
            if snippets:
                lead.evidence = snippets
                attached += 1
        if attached:
            audit.emit("rag.evidence_attached", leads=attached, corpus_size=len(corpus))

    def _persist_graph(self, delta: GraphDelta, audit: AuditLog) -> None:
        """Mirror the run's graph delta into Neo4j when that backend is selected.

        Best-effort durable persistence: the in-memory NetworkX store remains the
        source of truth for in-run analysis. Any failure degrades silently (logged)
        so an unreachable database never fails an otherwise-successful run.
        """
        if self.settings.graph_backend != "neo4j" or delta.is_empty():
            return
        try:
            from aetherseed.core.graph.neo4j_store import Neo4jGraphStore

            store = Neo4jGraphStore(self.settings)
            try:
                store.apply_delta(delta)
                audit.emit("graph.persisted", backend="neo4j",
                           nodes=len(delta.nodes), edges=len(delta.edges))
            finally:
                store.close()
        except Exception as exc:  # never fail the run for a persistence miss
            log.warning("graph.neo4j_persist_failed", error=str(exc))

    async def _maybe_screenshot(
        self, shotter: PlaywrightFetcher, url: str, session: Any, result: InvestigationRun
    ) -> int:
        if not shotter.available():
            return 0
        try:
            png = await shotter.screenshot(url)
        except AetherError:
            return 0
        except Exception as exc:
            log.debug("screenshot.failed", url=url, error=str(exc))
            return 0
        record = FilesystemAssetStore(self.settings).put(
            png, kind="screenshot", content_type="image/png", source_url=url
        )
        AssetRepository(session).record(
            result.run_id, kind="screenshot", path=record.path, sha256=record.sha256,
            size_bytes=record.size_bytes, content_type="image/png", source_url=url,
        )
        return 1

    async def _enrich(
        self,
        graph: NetworkXGraphStore,
        audit: AuditLog,
        run_entities: dict[str, Entity],
        run_relationships: list[Relationship],
    ) -> None:
        enrichers: list[Enricher] = get_enrichers(self.settings)
        targets = [e for e in list(run_entities.values())
                   if e.type in (EntityType.DOMAIN, EntityType.COMPANY)]
        for entity in targets[:50]:
            for enricher in enrichers:
                if not enricher.supports(entity):
                    continue
                try:
                    res = await enricher.enrich(entity)
                except Exception as exc:
                    log.warning("enrich.failed", enricher=enricher.name, error=str(exc))
                    continue
                if res.entities or res.relationships:
                    for ne in res.entities:
                        run_entities.setdefault(canonical_key(ne), ne)
                    run_relationships.extend(res.relationships)
                    graph.apply_delta(GraphDelta(nodes=res.entities, edges=res.relationships))
                    audit.emit("entity.enriched", entity=entity.label, enricher=enricher.name)

    # --------------------------------------------------------------- helpers

    def _build_leads(
        self,
        subject: SubjectSeed,
        expansion: Any,
        graph: NetworkXGraphStore,
        money: Any,
        run_entities: dict[str, Entity],
    ) -> list[Lead]:
        leads: list[Lead] = []
        prov = Provenance(extractor="pipeline.leads")

        for name in expansion.related_entities[:20]:
            leads.append(Lead(title=name, lead_type="entity", value=name,
                              summary="Related entity proposed by AetherMind.", provenance=[prov]))
        for hyp in expansion.money_hypotheses[:20]:
            leads.append(Lead(title=hyp, lead_type="money_trail", value=hyp,
                              summary="Follow-the-money hypothesis to test.", risk=0.4,
                              provenance=[prov]))
        # High-value discovered entities.
        for ent in run_entities.values():
            if ent.type in (EntityType.COMPANY, EntityType.PERSON):
                leads.append(Lead(
                    title=ent.label, lead_type="entity", value=ent.label,
                    summary=f"Discovered {ent.type.value} entity.",
                    confidence=ent.confidence, provenance=ent.provenance or [prov],
                ))
        # Red flags become high-risk leads.
        for flag in money.red_flags[:20]:
            leads.append(Lead(
                title=f"Red flag: {flag.signal}", lead_type="red_flag", value=flag.label,
                summary=flag.detail, why_it_matters=f"Signal: {flag.signal}.",
                risk=flag.severity, relevance=0.8, confidence=0.6, provenance=[prov],
            ))
        # De-dup by (type, value).
        dedup: dict[tuple[str, str], Lead] = {}
        for lead in leads:
            dedup.setdefault((lead.lead_type, lead.value.lower()), lead)
        return list(dedup.values())

    @staticmethod
    def _load_seen(session: Any, run_id: str) -> tuple[set[str], set[str]]:
        urls: set[str] = set()
        hashes: set[str] = set()
        for row in session.scalars(select(PageRecord).where(PageRecord.run_id == run_id)):
            urls.add(row.url.rstrip("/"))
            if row.content_hash:
                hashes.add(row.content_hash)
        return urls, hashes

    @staticmethod
    def _collect_assets(session: Any, run_id: str) -> AssetManifest:
        from aetherseed.schemas import AssetRecord

        rows = AssetRepository(session).list_by_run(run_id)
        return AssetManifest(
            assets=[
                AssetRecord(
                    id=r.id, kind=r.kind, path=r.path, content_type=r.content_type,
                    sha256=r.sha256, size_bytes=r.size_bytes, source_url=r.source_url,
                )
                for r in rows
            ]
        )

    @staticmethod
    def _count_pending_seeds(session: Any, run_id: str) -> int:
        return len(SeedRepository(session).list_by_run(run_id, status=SeedStatus.PENDING))

    @staticmethod
    def _finalize_status(result: InvestigationRun) -> None:
        if result.status is RunStatus.FAILED:
            return
        if result.metrics.failed and result.metrics.succeeded == 0 and result.metrics.processed:
            result.status = RunStatus.FAILED
        elif result.metrics.failed:
            result.status = RunStatus.PARTIAL
        else:
            result.status = RunStatus.SUCCEEDED
