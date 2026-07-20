# ADR 0005 — Structured output via JSON-schema repair loop

- Status: Accepted
- Date: 2026-07-20

## Context

Every AI output must be a validated Pydantic model, never free text. Libraries
like `instructor`/`outlines` do this well but assume specific clients and add
dependencies; local models vary in how reliably they emit valid JSON.

## Decision

Implement structured output in the backend itself: append the target model's
JSON Schema to the system prompt, extract the first balanced JSON object from the
completion, validate with Pydantic, and on failure retry up to 3 times with the
validation error fed back as a repair instruction. `instructor` remains available
(optional `ai` extra) for backends that support it.

## Consequences

- Works across arbitrary local models with only `httpx` + `pydantic`.
- Deterministic, testable (`test_structured_repair_loop`), and backend-agnostic.
- On persistent failure the engine falls back to deterministic heuristics rather
  than raising — preserving the "always structured, never hard-fail" guarantee.
