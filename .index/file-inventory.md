# File inventory (focused)

| Path | Purpose | Status |
|------|---------|--------|
| `aetherseed/core/seeding/engine.py` | Seed proposal + HITL approve/reject | Active |
| `aetherseed/core/acquisition/fetcher.py` | Static httpx fetcher with retries + rate limit | Active |
| `aetherseed/core/acquisition/ratelimit.py` | Per-host delay + global semaphore | Active |
| `aetherseed/core/acquisition/security.py` | SSRF resolve/validate | Active |
| `aetherseed/core/interfaces.py` | Protocols / swap seams | Active |
| `tests/test_seeding.py` | Seeding gate, approval, dedup, reject | Active |
| `tests/test_fetcher.py` | HttpxFetcher respx tests | Active |
| `ARCHITECTURE.md` | Canonical architecture doc | Active |
| `CLAUDE.md` | Agent/contributor rules | Active |
