"""In-memory test doubles (no network required)."""

from __future__ import annotations

import hashlib

from aetherseed.core.interfaces import FetchResult


class FakeFetcher:
    """A :class:`Fetcher` serving canned HTML from a dict of url -> html."""

    name = "fake"

    def __init__(self, pages: dict[str, str], *, content_type: str = "text/html") -> None:
        self.pages = pages
        self.content_type = content_type
        self.calls: list[str] = []

    async def fetch(self, url: str, *, render: bool = False) -> FetchResult:
        self.calls.append(url)
        norm = url.rstrip("/")
        html = self.pages.get(url) or self.pages.get(norm) or self.pages.get(url + "/")
        if html is None:
            return FetchResult(url=url, final_url=url, status_code=404, ok=False, error="404")
        body = html.encode("utf-8")
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content=body,
            content_type=self.content_type,
            ok=True,
            content_hash=hashlib.sha256(body).hexdigest(),
        )

    async def aclose(self) -> None:
        return None
