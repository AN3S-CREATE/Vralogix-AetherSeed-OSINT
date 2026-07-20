# Security & Responsible Use

AetherSeed is built for **lawful, authorised** investigative research. This
document covers the platform's security controls and your obligations.

## Responsible use

- Only collect information you are legally authorised to collect.
- Comply with applicable law (POPIA, GDPR, CFAA-equivalents), site terms of
  service, and robots directives.
- The platform performs **no** automatic authentication, credential stuffing, or
  login. Any authenticated access must be explicit, opt-in, and lawful.
- Do not use AetherSeed for harassment, stalking, doxxing, or targeting private
  individuals without lawful basis.

## Built-in controls

| Control | Where |
|---|---|
| **SSRF egress guard** — denies private/loopback/link-local/metadata targets; optional allowlist | `core/acquisition/security.py` |
| **robots.txt compliance** — honoured by default; override is per-run and audited | `core/acquisition/robots.py` |
| **Rate limiting** — per-host polite delay + global concurrency cap | `core/acquisition/ratelimit.py` |
| **Content validation** — content-type allow/deny, hard size cap, virus-scan hook | `core/acquisition/downloader.py` |
| **Retry policy** — only transient errors are retried (no amplification) | `core/acquisition/fetcher.py` |
| **Safety budgets** — max new seeds/hour, spend cap, approval gate | `core/seeding/budget.py` |
| **Hash-chained audit log** — tamper-evident record of every decision | `core/storage/audit.py` |
| **Content-addressable evidence locker** — SHA-256 named, integrity-verifiable | `core/storage/asset_store.py` |
| **Secrets** — env / Docker secrets only; never in code | `config.py` |
| **CORS + methods locked down** on the API | `apps/api/main.py` |

## Compliance (POPIA)

- **Data minimisation & retention:** `AETHERSEED_RETENTION_DAYS`.
- **PII redaction:** `AETHERSEED_PII_REDACTION` (redact in logs/exports).
- **Auditability:** all personal-data access and every seeding decision is
  logged with provenance in the hash-chained audit trail.
- **Least privilege:** workers run as a non-root user with only the data volume.

## Hardening checklist (production)

- Set a strong `POSTGRES_PASSWORD`; use Postgres, not SQLite.
- Restrict `AETHERSEED_CORS_ORIGINS` to known frontends.
- Set an `AETHERSEED_ACQ_EGRESS_ALLOWLIST` if crawling should be scoped.
- Run Playwright in an isolated container; keep the browser image patched.
- Put the API behind TLS + auth (reverse proxy); do not expose `/metrics`
  publicly.
- Rotate any cloud API keys; keep `AETHERSEED_AI_ENABLE_CLOUD_FALLBACK=false`
  unless required.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the Veralogix security contact.
Do not open a public issue for security reports. Please include reproduction
steps and impact.
