# ADR 0001 — Local-first with graceful degradation

- Status: Accepted
- Date: 2026-07-20

## Context

The platform targets a single power-user or small team on a workstation, handling
sensitive investigative data under POPIA. Cloud dependencies raise privacy, cost,
and offline-availability concerns. Contributors will not all have every optional
service (Ollama, a browser, Redis, Postgres, Neo4j) installed.

## Decision

Everything runs **locally by default** and every external/optional service is
**optional with graceful degradation**:

- No Ollama → AetherMind uses deterministic heuristics (regex NER, template seed
  expansion, rule-based gap analysis).
- No Playwright → static `httpx` fetching only; screenshots are skipped.
- No Redis/Celery → runs execute in-process.
- SQLite is the default database; Postgres is a config change.

Importing any module must never require a network service. Cloud LLMs are opt-in
behind a feature flag **and** an API key.

## Consequences

- The full test suite runs offline and deterministically (72 tests, no network).
- Slightly more code (a heuristic path beside each AI path), but the platform is
  always usable and never hard-fails for a missing backend.
- Output is always structured, whether produced by a model or a heuristic.
