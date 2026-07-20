"""AetherMind — the prospective AI engine.

Four capabilities, each with an LLM path (when a model is reachable) and a
deterministic fallback (always):

* :meth:`expand_seed`   — turn a subject into queries, handles, related entities,
  money hypotheses, and candidate seeds.
* :meth:`extract`       — entities + relationships from a page (LLM plus regex).
* :meth:`score_leads`   — relevance / novelty / risk / confidence per lead.
* :meth:`analyze_gaps`  — what is still missing and what to do next.

The engine never returns free text and never raises for lack of a model — that
is the local-first guarantee.
"""

from __future__ import annotations

from aetherseed.config import Settings, get_settings
from aetherseed.core.ai import prompts
from aetherseed.core.ai.backend import get_llm_backend
from aetherseed.core.ai.schemas import (
    LLMExtraction,
    LLMGapAnalysis,
    LLMLeadAssessment,
    LLMSeedCandidate,
    LLMSeedExpansion,
)
from aetherseed.core.interfaces import ExtractedContent, LLMBackend
from aetherseed.core.nlp import extract_entities
from aetherseed.errors import AetherError
from aetherseed.logging import get_logger
from aetherseed.schemas import (
    Entity,
    EntityType,
    GapReport,
    Lead,
    NextAction,
    Provenance,
    Relationship,
    RelationType,
    SubjectSeed,
)

log = get_logger(__name__)

_MAX_EXTRACT_CHARS = 6000
_IDEAL_DIMENSIONS = {
    EntityType.PERSON: "people / directors / beneficial owners",
    EntityType.COMPANY: "companies / corporate structure",
    EntityType.DOMAIN: "online presence / domains",
    EntityType.LOCATION: "physical locations / addresses",
    EntityType.TRANSACTION: "financial flows / transactions",
    EntityType.DOCUMENT: "supporting documents / filings",
}


def _coerce_entity_type(raw: str) -> EntityType:
    try:
        return EntityType(raw.strip().lower())
    except ValueError:
        return EntityType.OTHER


def _coerce_relation_type(raw: str) -> RelationType:
    try:
        return RelationType(raw.strip().lower())
    except ValueError:
        return RelationType.RELATED_TO


class AetherMind:
    """The prospective AI engine. Backend-agnostic; degrades gracefully."""

    def __init__(self, backend: LLMBackend | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._backend: LLMBackend = backend or get_llm_backend(self._settings)
        self.llm_calls = 0

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def uses_model(self) -> bool:
        """Whether a real model backs this engine (vs. deterministic heuristics)."""
        return self._backend.available() and self._backend.name != "null"

    # --- Seed expansion ------------------------------------------------------

    async def expand_seed(self, subject: SubjectSeed) -> LLMSeedExpansion:
        """Propose next lines of inquiry for ``subject``."""
        if self.uses_model():
            prompt = prompts.get_prompt("seed_expansion").render(
                subject_type=subject.subject_type.value,
                identifiers=", ".join(subject.primary_identifiers),
                context=subject.context or "(none provided)",
            )
            try:
                self.llm_calls += 1
                return await self._backend.structured(prompt, LLMSeedExpansion)
            except AetherError as exc:
                log.warning("ai.expand_seed_fallback", error=str(exc))
        return self._heuristic_expansion(subject)

    def _heuristic_expansion(self, subject: SubjectSeed) -> LLMSeedExpansion:
        ids = subject.primary_identifiers
        queries: list[str] = []
        for ident in ids:
            queries.append(ident)
            queries.append(f'"{ident}"')
            if subject.subject_type.value == "company":
                queries += [
                    f"{ident} directors",
                    f"{ident} ownership",
                    f"{ident} annual report",
                    f"{ident} beneficial owner",
                ]
            elif subject.subject_type.value == "person":
                queries += [f"{ident} company", f"{ident} director", f"{ident} linkedin"]
        handles = [
            f"@{ident.split()[0].lower()}" for ident in ids if ident and ident[0].isalpha()
        ]
        candidates = [
            LLMSeedCandidate(
                subject_type="domain",
                identifiers=[ident],
                rationale="identifier looks like a domain",
                priority=0.6,
            )
            for ident in ids
            if "." in ident and " " not in ident
        ]
        money_hypotheses = (
            [
                f"Test whether {ids[0]} shares directors with related entities",
                f"Test whether {ids[0]} has undisclosed beneficial owners",
            ]
            if subject.subject_type.value in {"company", "person"}
            else []
        )
        return LLMSeedExpansion(
            search_queries=list(dict.fromkeys(queries))[:20],
            social_handles=handles,
            related_entities=[],
            money_hypotheses=money_hypotheses,
            candidate_seeds=candidates,
        )

    # --- Entity / relation extraction ---------------------------------------

    async def extract(
        self, content: ExtractedContent
    ) -> tuple[list[Entity], list[Relationship]]:
        """Extract entities + relationships, merging LLM and regex results."""
        # Deterministic floor (already computed by the HTML extractor, but be safe).
        entities: list[Entity] = list(content.entities) or extract_entities(
            content.text, source_url=content.url
        )
        relationships: list[Relationship] = list(content.relationships)

        if self.uses_model() and content.text.strip():
            prompt = prompts.get_prompt("entity_extraction").render(
                url=content.url, text=content.text[:_MAX_EXTRACT_CHARS]
            )
            try:
                self.llm_calls += 1
                llm = await self._backend.structured(prompt, LLMExtraction)
                add_e, add_r = self._map_extraction(llm, content.url)
                entities = self._merge_entities(entities, add_e)
                relationships.extend(add_r)
            except AetherError as exc:
                log.warning("ai.extract_fallback", url=content.url, error=str(exc))

        return entities, relationships

    def _map_extraction(
        self, llm: LLMExtraction, url: str
    ) -> tuple[list[Entity], list[Relationship]]:
        prov = Provenance(source_url=url, extractor="ai.llm")
        by_name: dict[str, Entity] = {}
        for le in llm.entities:
            ent = Entity(
                type=_coerce_entity_type(le.type),
                label=le.name.strip(),
                attributes=dict(le.attributes),
                confidence=le.confidence,
                provenance=[prov],
            )
            by_name[le.name.strip().lower()] = ent

        rels: list[Relationship] = []
        for lr in llm.relationships:
            src = by_name.get(lr.source.strip().lower())
            dst = by_name.get(lr.target.strip().lower())
            if not src:
                src = Entity(type=EntityType.OTHER, label=lr.source.strip(), provenance=[prov])
                by_name[lr.source.strip().lower()] = src
            if not dst:
                dst = Entity(type=EntityType.OTHER, label=lr.target.strip(), provenance=[prov])
                by_name[lr.target.strip().lower()] = dst
            rels.append(
                Relationship(
                    source_id=src.id,
                    target_id=dst.id,
                    type=_coerce_relation_type(lr.type),
                    confidence=lr.confidence,
                    provenance=[prov],
                )
            )
        return list(by_name.values()), rels

    @staticmethod
    def _merge_entities(base: list[Entity], extra: list[Entity]) -> list[Entity]:
        index: dict[tuple[str, str], Entity] = {
            (e.type.value, e.label.lower()): e for e in base
        }
        for e in extra:
            key = (e.type.value, e.label.lower())
            if key in index:
                index[key].confidence = max(index[key].confidence, e.confidence)
                index[key].provenance.extend(e.provenance)
            else:
                index[key] = e
        return list(index.values())

    # --- Lead scoring --------------------------------------------------------

    async def score_leads(self, subject: SubjectSeed, leads: list[Lead]) -> list[Lead]:
        """Score each lead's relevance / novelty / risk / confidence in place."""
        for lead in leads:
            if self.uses_model():
                prompt = prompts.get_prompt("lead_scoring").render(
                    subject=self._subject_repr(subject),
                    lead=f"{lead.title}: {lead.summary} [{lead.value}]",
                )
                try:
                    self.llm_calls += 1
                    a = await self._backend.structured(prompt, LLMLeadAssessment)
                    lead.relevance, lead.novelty = a.relevance, a.novelty
                    lead.risk, lead.confidence = a.risk, a.confidence
                    if a.why_it_matters:
                        lead.why_it_matters = a.why_it_matters
                    continue
                except AetherError as exc:
                    log.warning("ai.score_lead_fallback", error=str(exc))
            self._heuristic_score(subject, lead)
        return leads

    def _heuristic_score(self, subject: SubjectSeed, lead: Lead) -> None:
        blob = f"{lead.title} {lead.summary} {lead.value}".lower()
        terms = {t.lower() for ident in subject.primary_identifiers for t in ident.split()}
        terms |= {w.lower() for w in subject.context.split()}
        hits = sum(1 for t in terms if t and len(t) > 2 and t in blob)
        lead.relevance = min(1.0, 0.3 + 0.15 * hits)
        if not lead.why_it_matters:
            lead.why_it_matters = "Matches subject identifiers/context." if hits else "Peripheral finding."

    # --- Gap analysis --------------------------------------------------------

    async def analyze_gaps(
        self, subject: SubjectSeed, entities: list[Entity], *, coverage_note: str = ""
    ) -> GapReport:
        """Assess what the investigation still lacks and recommend next actions."""
        if self.uses_model():
            sample = ", ".join(sorted({e.label for e in entities})[:40]) or "(none yet)"
            prompt = prompts.get_prompt("gap_analysis").render(
                subject=self._subject_repr(subject),
                entities=sample,
                coverage=coverage_note or "initial pass",
            )
            try:
                self.llm_calls += 1
                g = await self._backend.structured(prompt, LLMGapAnalysis)
                return GapReport(
                    coverage_score=g.coverage_score,
                    missing_dimensions=g.missing_dimensions,
                    unanswered_questions=g.unanswered_questions,
                    recommended_actions=[
                        NextAction(
                            action=a.action,
                            target=a.target,
                            rationale=a.rationale,
                            priority=a.priority,
                        )
                        for a in g.recommended_actions
                    ],
                    notes="AI-generated gap analysis.",
                )
            except AetherError as exc:
                log.warning("ai.gap_fallback", error=str(exc))
        return self._heuristic_gaps(subject, entities)

    def _heuristic_gaps(self, subject: SubjectSeed, entities: list[Entity]) -> GapReport:
        present = {e.type for e in entities}
        missing_dims = [label for etype, label in _IDEAL_DIMENSIONS.items() if etype not in present]
        coverage = 1.0 - len(missing_dims) / max(1, len(_IDEAL_DIMENSIONS))
        actions: list[NextAction] = []
        if EntityType.DOMAIN in present:
            actions.append(
                NextAction(
                    action="enrich_whois",
                    target=next(e.label for e in entities if e.type is EntityType.DOMAIN),
                    rationale="Resolve registrant / infrastructure ownership.",
                    priority=0.7,
                )
            )
        if subject.subject_type.value == "company" and EntityType.PERSON not in present:
            actions.append(
                NextAction(
                    action="search_registry",
                    target=subject.primary_identifiers[0],
                    rationale="No directors/officers found yet — query company registry (e.g. CIPC).",
                    priority=0.8,
                )
            )
        questions = [f"Who ultimately controls {subject.primary_identifiers[0]}?"]
        if EntityType.TRANSACTION not in present:
            questions.append("What financial flows connect the known entities?")
        return GapReport(
            coverage_score=round(coverage, 3),
            missing_dimensions=missing_dims,
            unanswered_questions=questions,
            recommended_actions=actions,
            notes="Heuristic gap analysis (no model backend).",
        )

    @staticmethod
    def _subject_repr(subject: SubjectSeed) -> str:
        return (
            f"{subject.subject_type.value}: {', '.join(subject.primary_identifiers)} "
            f"— {subject.context}"
        )
