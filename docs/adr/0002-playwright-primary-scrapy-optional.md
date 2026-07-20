# ADR 0002 — Playwright primary (for JS), httpx+bs4 default, Scrapy optional

- Status: Accepted
- Date: 2026-07-20

## Context

Investigative targets mix static HTML and JS-rendered SPAs, and often need
screenshots/PDF capture and stealth/fingerprint controls. Scrapy is excellent for
very high-volume static crawls but is a heavyweight framework with its own event
loop and a steeper path to JS rendering and screenshots.

## Decision

- Default acquisition is **`httpx` + BeautifulSoup/lxml** — light, async, easy to
  mock and test.
- **Playwright** is the primary engine for JS rendering, screenshots, and stealth
  (optional `browser` extra; graceful when absent).
- Scrapy is left as an **optional** high-volume path behind the same `Fetcher`
  seam rather than the core.

## Consequences

- The core stays light and fully testable offline (respx-mocked httpx).
- Screenshots/PDF and stealth come "for free" with Playwright when needed.
- High-volume Scrapy pipelines can be added later without reworking the crawler.
