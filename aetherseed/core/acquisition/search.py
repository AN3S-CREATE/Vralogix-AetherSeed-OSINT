"""Search providers — turn a query into candidate seed URLs.

This is what lets a *bare name* ("Example Mining Pty Ltd") seed a crawl, not just
a URL/domain. Providers implement a small protocol so the backend is swappable:

* :class:`DuckDuckGoSearchProvider` — no API key; scrapes the HTML endpoint.
* :class:`SearxSearchProvider` — points at a self-hosted SearXNG (JSON API).
* :class:`NullSearchProvider` — returns nothing (the default; search is opt-in).

Search is **off by default** to preserve the offline-first posture; enable it via
``AETHERSEED_SEARCH_BACKEND`` and the ``--search`` flag. Result URLs are still
subject to the crawler's SSRF/robots/rate controls when fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from aetherseed.config import Settings, get_settings
from aetherseed.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        """Return candidate results for ``query`` (best-effort, never raises)."""
        ...


class NullSearchProvider:
    """Disabled search: always returns an empty list."""

    name = "none"

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        return []


class DuckDuckGoSearchProvider:
    """Keyless search via DuckDuckGo's HTML endpoint.

    Parses the lightweight ``html.duckduckgo.com`` results page. This is a
    courtesy endpoint — keep volume low and honour rate limits.
    """

    name = "duckduckgo"
    _ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        headers = {"User-Agent": self._settings.acq_user_agent}
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.acq_request_timeout_s,
                headers=headers,
                proxy=self._settings.acq_proxy_url,
                follow_redirects=True,
            ) as client:
                resp = await client.post(self._ENDPOINT, data={"q": query})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("search.ddg_failed", query=query, error=str(exc))
            return []
        return self._parse(resp.text, max_results)

    @staticmethod
    def _parse(html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        out: list[SearchResult] = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            url = DuckDuckGoSearchProvider._unwrap(str(href))
            if not url:
                continue
            out.append(SearchResult(url=url, title=a.get_text(strip=True)))
            if len(out) >= max_results:
                break
        return out

    @staticmethod
    def _unwrap(href: str) -> str | None:
        """DDG wraps targets as ``//duckduckgo.com/l/?uddg=<encoded>``; unwrap it."""
        parsed = urlparse(href if href.startswith("http") else f"https:{href}")
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [])
            if target:
                return unquote(target[0])
            return None
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return parsed.geturl()
        return None


class SearxSearchProvider:
    """Search via a self-hosted SearXNG instance (JSON API)."""

    name = "searxng"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base = (self._settings.searxng_url or "").rstrip("/")

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        if not self._base:
            log.warning("search.searxng_unconfigured")
            return []
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.acq_request_timeout_s,
                headers={"User-Agent": self._settings.acq_user_agent},
                proxy=self._settings.acq_proxy_url,
            ) as client:
                resp = await client.get(
                    f"{self._base}/search", params={"q": query, "format": "json"}
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("search.searxng_failed", query=query, error=str(exc))
            return []
        results = data.get("results", [])[:max_results]
        return [
            SearchResult(url=r["url"], title=r.get("title", ""), snippet=r.get("content", ""))
            for r in results
            if r.get("url")
        ]


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    """Return the configured search provider (``none`` by default)."""
    s = settings or get_settings()
    if s.search_backend == "duckduckgo":
        return DuckDuckGoSearchProvider(s)
    if s.search_backend == "searxng":
        return SearxSearchProvider(s)
    return NullSearchProvider()
