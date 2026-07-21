# Architecture overview

Local-first OSINT platform: FastAPI + SQLAlchemy + Playwright (optional) + Ollama (optional) + NetworkX.

## Layers

- **Acquisition** — SSRF guard, robots, rate limit, `HttpxFetcher` / `PlaywrightFetcher`, crawler with per-item fault isolation.
- **AI (AetherMind)** — pluggable LLM backends; structured Pydantic outputs only; heuristics when offline.
- **Graph** — NetworkX store, entity resolution, follow-the-money.
- **Seeding** — budgeted auto-seed with human-in-the-loop approve/reject gate.
- **Storage** — SQLAlchemy models/repos, asset store, hash-chained audit log.
- **Pipelines** — `investigation.py` end-to-end orchestration.
- **Apps** — API, optional worker, optional web UI.

## Invariants

- Optional backends must not break imports; graceful degradation.
- All outbound fetches through SSRF guard; no auto-login/credential stuffing.
- Single item failure never aborts a run.
