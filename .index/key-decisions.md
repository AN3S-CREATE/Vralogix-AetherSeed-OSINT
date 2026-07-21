# Key decisions

## 2026-07-21 — HITL reject requires PENDING

- **Decision:** `SeedingEngine.reject()` must refuse non-`PENDING` seeds (same as `approve()`).
- **Rationale:** Only pending seeds are actionable under the human-in-the-loop gate.

## 2026-07-21 — Rate limiter release ownership in HttpxFetcher

- **Decision:** Acquire/release are owned entirely inside `_fetch_with_retry` attempts; `fetch()` must not release in `finally`.
- **Rationale:** Error paths already release before retry/raise; an outer finally caused `Semaphore released too many times` when the retry budget was exhausted.
