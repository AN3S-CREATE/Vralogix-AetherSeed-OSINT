# Architecture

AetherSeed is a layered, local-first system. Each layer depends only on the
layer below and on the Protocols in `aetherseed/core/interfaces.py`, so any
concrete backend (scraper, LLM, enricher, graph store, asset store, vector
index) can be swapped without touching orchestration.

## Package map

```
aetherseed/
├── config.py            # pydantic-settings (env-driven, no secrets in code)
├── logging.py           # structlog + correlation IDs (run/seed/page/worker)
├── errors.py            # error taxonomy: transient | permanent | policy
├── schemas.py           # I/O contract: SubjectSeed → InvestigationRun
├── core/
│   ├── interfaces.py    # Protocols + transport value objects (the swap seam)
│   ├── nlp.py           # deterministic regex entity extraction (no model)
│   ├── acquisition/     # security(SSRF) · ratelimit · robots · fetcher ·
│   │                    #   browser(Playwright) · extract(bs4) · downloader ·
│   │                    #   crawler(priority frontier, fault isolation)
│   ├── ai/              # backend(Ollama/Anthropic/Null) · prompts · schemas ·
│   │                    #   engine(AetherMind: expand/extract/score/gaps)
│   ├── rag/             # retrieval(BM25+dense+RRF) · index(memory/chroma/null)
│   ├── graph/           # store(NetworkX) · factory(→neo4j) · resolution · money
│   ├── seeding/         # rules · budget · engine (auto-seed under HITL gate)
│   ├── enrichment/      # DnsEnricher (+ WHOIS/registry stubs)
│   └── storage/         # models · db · repositories · asset_store · audit
├── pipelines/           # investigation.py — end-to-end orchestration
└── apps/
    ├── api/             # FastAPI service + background run registry
    ├── worker/          # optional Celery tasks
    └── web/             # optional UI (Streamlit/Next.js)
```

## The six pillars

1. **Acquisition** — `HttpxFetcher` (static) and `PlaywrightFetcher` (JS +
   screenshots, optional) sit behind the `Fetcher` protocol. Every request
   passes the **SSRF guard** (private/loopback/link-local denied; egress
   allowlist), **robots.txt** (overridable per run), and a **rate limiter**
   (per-host polite delay + global concurrency). The `Crawler` drives a priority
   frontier with configurable depth/breadth/domain scope, dedup by URL and
   content hash, and per-item fault isolation.

2. **AetherMind (AI)** — `AetherMind` orchestrates a pluggable `LLMBackend`.
   Default is Ollama over HTTP (no heavy client dep); cloud (Anthropic) is behind
   a feature flag; `NullBackend` forces deterministic heuristics. Structured
   output is enforced by asking for a JSON object matching a Pydantic schema with
   a validate-and-repair retry loop. Capabilities: seed expansion, entity/relation
   extraction (LLM ∪ regex), lead scoring, gap analysis — each with a heuristic
   fallback so output is always structured and never fails for lack of a model.

3. **Graph & follow-the-money** — `NetworkXGraphStore` runs entity resolution
   (deterministic canonical keys + `rapidfuzz` fuzzy merge) on every insert.
   Exports to node-link / Cytoscape / JSON-LD / GraphML. `get_graph_store`
   selects the backend (NetworkX by default; a durable `Neo4jGraphStore` behind
   the same Protocol when configured, degrading if unreachable). `FollowTheMoney`
   surfaces ownership/control chains, shared-director networks, payment flows,
   a chronological **timeline**, **geo** points, and heuristic red flags
   (circular ownership, central intermediaries, shell-like nodes, director hubs)
   — with centrality computed once per analysis.

   **RAG grounding** — `core/rag` indexes each crawled page into a per-run
   corpus (`HybridRetriever`: BM25 + optional dense, fused with RRF) and attaches
   the top supporting snippets to every lead as auditable `EvidenceSnippet`s.
   Offline by default (`memory` backend); `chroma` is a dense-embedding upgrade.

4. **Automated seeding** — `SeedingEngine` combines rule-based candidates
   (`rules.py`, YAML-overridable), LLM proposals, and gap actions. Each candidate
   is de-duplicated, checked against a `SafetyBudget` (max new seeds/hour, spend
   cap), gated by human approval, and recorded in `seed_decisions` + the audit
   log. Nothing is created silently.

5. **Gap loop** — gap analysis compares known entity dimensions against an ideal
   coverage set and emits missing dimensions, unanswered questions, and
   prioritised next actions that feed back into seeding.

6. **Workspace & storage** — SQLAlchemy models persist runs, seeds, pages,
   entities/edges, assets, failures (DLQ), checkpoints, and decisions. The
   content-addressable `FilesystemAssetStore` is the evidence locker; the
   hash-chained `AuditLog` is the tamper-evident record.

## Data flow (one run)

```
run() ─┬─ register run + root seed + audit "run.started"
       ├─ AetherMind.expand_seed
       ├─ derive crawlable seed URLs (identifiers ∪ candidate seeds)
       ├─ Crawler.crawl ──▶ per page: record · extract · graph.apply_delta ·
       │                    checkpoint · (optional screenshot) · fail→DLQ
       ├─ optional enrichment pass (DNS/WHOIS/registry)
       ├─ FollowTheMoney.analyze
       ├─ build + score leads
       ├─ AetherMind.analyze_gaps
       ├─ optional SeedingEngine.propose (budgeted, HITL)
       └─ persist graph_delta + assets; finalise status; audit "run.finished"
```

## Resilience model

- **Error taxonomy** drives behaviour: `transient` → retried (tenacity,
  exponential backoff + jitter); `permanent` → skipped and DLQ'd; `policy` →
  refused and surfaced.
- **Checkpointing**: a watermark per run; on resume, previously-fetched pages
  seed the crawler's dedup sets, so a crashed job continues where it stopped.
- **Fail-fast**: a configurable failure-rate threshold ends a run gracefully as
  `partial`/`failed` with progress preserved.

## Concurrency & scaling

Async-first acquisition with a bounded worker pool and per-host politeness. The
storage layer is synchronous (short transactions) for simplicity and robustness.
Horizontal scale-out is available via the optional Celery worker + Redis; the
graph and vector store can be moved to Neo4j and Chroma respectively behind the
same interfaces.

## Extension points

Implement the relevant Protocol and inject it:

| To add… | Implement | Inject via |
|---|---|---|
| a scraper/renderer | `Fetcher` | `InvestigationPipeline(fetcher_factory=…)` |
| an LLM backend | `LLMBackend` | `AetherMind(backend=…)` |
| an enricher | `Enricher` | `core/enrichment/enrichers.py` registry |
| a graph store | `GraphStore` | pipeline construction |
| an extractor | `ContentExtractor` | `Crawler(extractor=…)` |

See [`docs/adr/`](docs/adr/) for the reasoning behind the major choices.
