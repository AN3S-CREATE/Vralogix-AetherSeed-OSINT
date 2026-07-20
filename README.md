# Veralogix AetherSeed OSINT

**Local-first, resilient, auditable investigative research platform.** Multi-modal
scraping / crawling / screenshots, a prospective AI engine ("AetherMind"),
automated seeding with gap analysis, and a knowledge graph with follow-the-money
intelligence. Privacy-first and POPIA-aware. Built for real-world OSINT, mining,
and legal workflows.

> ⚖️ **Legal & ethical use only.** This tool is for lawful investigative research
> on information you are authorised to collect. You are responsible for complying
> with applicable law (incl. POPIA/GDPR), site terms, and robots directives.
> There is **no** auto-login or credential stuffing. See
> [`SECURITY.md`](SECURITY.md) and [Compliance](#compliance-popia).

---

## Why it's different

- **Runs offline.** Local LLMs via Ollama; no data leaves your machine by
  default. Cloud models are opt-in behind a feature flag + API key.
- **Degrades gracefully.** No Ollama? Deterministic heuristics take over. No
  Playwright? Static fetching still works. Nothing hard-fails for a missing
  optional backend.
- **Resilient & resumable.** Per-item fault isolation, a dead-letter queue,
  checkpointed watermarks, exponential-backoff retries for transient errors.
- **Auditable.** Every run has a hash-chained, tamper-evident audit log; every
  fact carries provenance.
- **Safe by default.** SSRF egress guard, robots.txt compliance, rate limiting,
  content-type/size validation, and safety budgets on auto-seeding.

## Architecture at a glance

```
SubjectSeed ──▶ AetherMind expand ──▶ Crawl (priority frontier, SSRF-guarded)
                                          │
                    entity/relation extraction (LLM ∪ regex)
                                          ▼
                 Knowledge graph (NetworkX) ──▶ Follow-the-money
                                          │
              Leads (scored) · Gap analysis · Auto-seeding (budgeted, HITL)
                                          ▼
                         InvestigationRun (structured, exportable)
```

Full design: [`ARCHITECTURE.md`](ARCHITECTURE.md). Decisions:
[`docs/adr/`](docs/adr/).

---

## Quickstart (local, no Docker)

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev                 # install core + dev deps
cp .env.example .env                # optional; sane defaults work as-is
uv run aetherseed doctor            # check DB / Ollama / Playwright
```

Run an investigation against a site you control or are authorised to test:

```bash
uv run aetherseed investigate \
  --subject "https://example.com/" \
  --type company \
  --context "Ownership and connections investigation" \
  --max-depth 2 --auto-seed --require-approval \
  --output ./runs/example
```

Outputs a scored lead list, a gap report, and (to `--output`) `run.json`,
`graph.graphml`, `graph.jsonld`, and `leads.json`.

### Optional local AI (recommended)

```bash
# Install Ollama (https://ollama.com), then:
ollama pull llama3.1
ollama pull nomic-embed-text
# AetherMind auto-detects Ollama on http://localhost:11434 and upgrades from
# heuristics to full LLM extraction / seed expansion / gap analysis.
```

### Optional browser (JS-rendered pages + screenshots)

```bash
uv sync --extra browser
uv run playwright install chromium
uv run aetherseed investigate --subject https://spa.example/ --render --screenshots ...
```

---

## Quickstart (Docker Compose — full stack)

```bash
docker compose up api ollama                 # minimal, offline-capable
docker compose exec ollama ollama pull llama3.1
docker compose --profile worker up           # + Celery worker + Redis
docker compose --profile full up             # everything (+ Postgres)
```

API docs at http://localhost:8000/docs. Health at `/health`, Prometheus metrics
at `/metrics`.

---

## Using the API

```bash
# Start a run (returns immediately with a run_id to poll)
curl -s -X POST http://localhost:8000/v1/investigations \
  -H 'content-type: application/json' \
  -d '{"subject": {"subject_type": "company",
                    "primary_identifiers": ["https://example.com/"],
                    "context": "ownership"},
       "auto_seed": true}'

curl -s http://localhost:8000/v1/investigations/<run_id>            # status
curl -s http://localhost:8000/v1/investigations/<run_id>/result     # full result
curl -s "http://localhost:8000/v1/investigations/<run_id>/graph?fmt=graphml"
```

## Input / output contract

**Input** — `SubjectSeed`:

```json
{
  "subject_type": "person|company|domain|event|custom",
  "primary_identifiers": ["name", "aliases", "domains", "socials"],
  "context": "short investigation brief",
  "constraints": {"max_depth": 3, "max_seeds": 50, "budget_usd": 0,
                  "require_approval": true},
  "existing_graph_id": null
}
```

**Output** — `InvestigationRun`: `run_id`, `status`, `metrics`, scored
`new_leads[]`, `graph_delta` (nodes + edges), `gap_report`,
`next_recommended_actions[]`, `asset_manifest`, and `audit_log_ref`. On error, a
structured envelope with `category` (transient / permanent / policy) and a
`retryable` flag; partial progress is always preserved.

---

## CLI reference

| command | purpose |
|---|---|
| `investigate` | Run a full investigation (see `--help` for all flags). |
| `runs` | List recent runs. |
| `seeds <run_id> [--approve ID] [--reject ID]` | Human-in-the-loop seed review. |
| `doctor` | Check DB / Ollama / Playwright health. |
| `prompts` | List the versioned AI prompt library. |
| `serve` | Run the FastAPI service. |

## Development

```bash
uv run pytest                       # 72 tests, fully offline
uv run pytest --cov=aetherseed      # with coverage
uv run ruff check aetherseed tests  # lint
uv run mypy aetherseed              # strict type-check
uv run alembic upgrade head         # apply DB migrations (prod)
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for extension points (new scrapers,
enrichers, LLM backends, graph stores).

## Compliance (POPIA)

- **Data minimisation & retention:** `AETHERSEED_RETENTION_DAYS`,
  `AETHERSEED_PII_REDACTION`.
- **Auditability:** every personal-data access and seeding decision is logged in
  a hash-chained audit trail.
- **Least privilege:** workers hold only what they need; secrets come from the
  environment / Docker secrets only.
- **Human-in-the-loop:** auto-seeding respects approval gates and safety budgets.

## Repository mirrors

Commits are mirrored to three GitHub remotes. Use `scripts/push_all.sh` (or
`.ps1`) after committing — see [`CLAUDE.md`](CLAUDE.md).

## License

Dual-licensed: PolyForm Noncommercial 1.0.0 for non-commercial use, commercial
license available from Veralogix. See [`LICENSE`](LICENSE).
