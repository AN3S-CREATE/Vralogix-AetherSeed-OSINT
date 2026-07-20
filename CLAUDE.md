# CLAUDE.md — agent & contributor rules for this repository

Guidance for Claude Code (and any coding agent) working in **Veralogix
AetherSeed OSINT**. Read this before making changes.

## Project

Local-first, resilient, auditable OSINT investigative research platform. Python
3.12+, FastAPI, SQLAlchemy 2.0, Playwright, local LLMs via Ollama, NetworkX
knowledge graph. The platform runs **fully offline** with graceful degradation
(no Ollama → deterministic heuristics; no Playwright → static fetch only).

## ⚠️ Git rule — MANDATORY multi-remote push

This repository is mirrored across **three** GitHub remotes. Whenever a commit is
made to this repo, it **must be committed and pushed to all three**:

| remote name | URL |
|-------------|-----|
| `an3s` (origin) | https://github.com/AN3S-CREATE/Vralogix-AetherSeed-OSINT.git |
| `veralogix`     | https://github.com/veralogix-group-innovation/Vralogix-AetherSeed-OSINT.git |
| `catalyst`      | https://github.com/VeralogixCatalyst/Vralogix-AetherSeed-OSINT.git |

**How:** after committing, run the helper which pushes the current branch to
every remote individually (each is pushed separately so one failing remote does
not block the others):

```bash
# POSIX / Git Bash
./scripts/push_all.sh

# Windows PowerShell
./scripts/push_all.ps1
```

The helper adds any missing remotes automatically. Do **not** push to only one
remote. Agents must never invent new remotes or force-push without explicit
human instruction. Pushing is an outward action — only push when the human has
asked you to (or has durably authorized it); otherwise commit locally and tell
them to run the helper.

## Dev commands

```bash
uv sync --extra dev            # install
uv run pytest                  # tests (72, offline)
uv run ruff check aetherseed tests
uv run mypy aetherseed         # strict, must stay clean
uv run aetherseed doctor       # backend health
uv run aetherseed investigate --subject "<name>" --type company --context "..."
uv run aetherseed serve        # FastAPI on :8000
```

## Invariants — do not break

- **Local-first + graceful degradation.** Every optional backend (Ollama,
  Playwright, Redis, Neo4j, Postgres) must be optional. Importing any module
  must never require a network service.
- **Structured output only.** The AI engine returns Pydantic models, never free
  text. Add new outputs as schemas in `aetherseed/schemas.py` or
  `aetherseed/core/ai/schemas.py`.
- **Safety is non-negotiable.** All outbound fetches go through the SSRF guard
  (`core/acquisition/security.py`). Respect robots.txt unless the run explicitly
  overrides it. Never add auto-login/credential-stuffing.
- **Fault isolation + audit.** A single item failure must never abort a run; log
  it to `failed_items`. Every material decision is appended to the hash-chained
  audit log.
- **No hardcoded secrets.** Config comes from `pydantic-settings` / env only.
- **Keep the gates green.** `pytest`, `ruff`, and `mypy --strict` must pass
  before you consider a change done.
- **Swap via interfaces.** New scrapers/enrichers/LLM backends/graph stores
  implement the Protocols in `core/interfaces.py`.

## Layout

`aetherseed/core/{acquisition,ai,graph,seeding,enrichment,storage}` — domain
layers. `aetherseed/pipelines/` — orchestration. `aetherseed/apps/{api,worker,web}`
— entrypoints. `db/` — Alembic. `prompts/` — versioned prompts. See
`ARCHITECTURE.md`.
