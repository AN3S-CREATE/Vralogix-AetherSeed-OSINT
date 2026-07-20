# Contributing to AetherSeed

Thanks for helping build a resilient, auditable, local-first OSINT platform.

## Ground rules

- **Keep the gates green.** `pytest`, `ruff check`, and `mypy aetherseed`
  (strict) must all pass. New code needs tests; core modules target ≥85% coverage.
- **Local-first.** Never make an optional backend mandatory. Importing a module
  must not require a network service.
- **Structured & audited.** AI outputs are Pydantic models. Material decisions
  are emitted to the audit log. Facts carry provenance.
- **Safe.** Outbound fetches go through the SSRF guard. Respect robots by
  default. No auto-login / credential stuffing.
- **No secrets in code.** Configuration comes from `pydantic-settings` / env.

## Setup

```bash
uv sync --extra dev
uv run pytest
```

## Workflow

1. Branch from `main`.
2. Make the change with tests + docstrings (Google/NumPy style with examples on
   public APIs).
3. Run the gates:
   ```bash
   uv run ruff check aetherseed tests --fix
   uv run mypy aetherseed
   uv run pytest --cov=aetherseed
   ```
4. Commit, then push to **all mirrors**: `./scripts/push_all.sh` (see `CLAUDE.md`).

## Extension points

All extensibility flows through the Protocols in `aetherseed/core/interfaces.py`.

### Add a scraper / renderer
Implement `Fetcher` (`fetch`, `aclose`). Inject with
`InvestigationPipeline(fetcher_factory=lambda respect_robots: MyFetcher(...))`.

### Add an enricher
Implement `Enricher` (`name`, `supports`, `enrich`) and register it in
`core/enrichment/enrichers.py`. See `DnsEnricher` for a working example and the
WHOIS/registry stubs for the intended shape.

### Add an LLM backend
Implement `LLMBackend` (`available`, `complete`, `structured`, `embed`). Reuse
`_StructuredMixin` for the JSON-schema repair loop. Wire it into
`core/ai/backend.get_llm_backend`.

### Add a graph store
Implement `GraphStore` (see `NetworkXGraphStore`). A Neo4j implementation is the
canonical next backend.

### Add a seeding rule / hook
Add a rule to `config/seed_rules.yaml`, or register a Python hook with
`@register_hook("name")` in `core/seeding/rules.py`.

### Change a prompt
Edit the matching file in `prompts/` (it overrides the packaged default at
runtime). Bump its `version` in the frontmatter.

## Database migrations

Change ORM models in `core/storage/models.py`, then:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

## Tests

- Unit tests per core module; property-based tests (`hypothesis`) for resolution
  and seed expansion.
- Network is mocked (`respx`) or replaced with in-memory fakes (`tests/fakes.py`).
  Tests must run offline and deterministically.
