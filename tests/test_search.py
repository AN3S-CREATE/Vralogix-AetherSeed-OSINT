"""Search-provider tests (respx, no network)."""

from __future__ import annotations

import httpx
import respx
from aetherseed.config import Settings
from aetherseed.core.acquisition.search import (
    DuckDuckGoSearchProvider,
    NullSearchProvider,
    SearxSearchProvider,
    get_search_provider,
)

_DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Facme.example%2Fabout">Acme About</a>
<a class="result__a" href="https://direct.example/page">Direct</a>
<a class="result__snippet" href="#">noise</a>
</body></html>
"""


async def test_null_provider_returns_empty() -> None:
    assert await NullSearchProvider().search("anything") == []


@respx.mock
async def test_duckduckgo_parses_and_unwraps() -> None:
    respx.post("https://html.duckduckgo.com/html/").mock(
        return_value=httpx.Response(200, html=_DDG_HTML)
    )
    results = await DuckDuckGoSearchProvider(Settings()).search("acme", max_results=10)
    urls = [r.url for r in results]
    assert "https://acme.example/about" in urls  # uddg-unwrapped
    assert "https://direct.example/page" in urls


@respx.mock
async def test_searxng_json() -> None:
    respx.get("https://searx.example/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"url": "https://a.example", "title": "A"}]}
        )
    )
    provider = SearxSearchProvider(Settings(searxng_url="https://searx.example"))
    results = await provider.search("x")
    assert results[0].url == "https://a.example"


def test_factory_selects_backend() -> None:
    assert get_search_provider(Settings(search_backend="none")).name == "none"
    assert get_search_provider(Settings(search_backend="duckduckgo")).name == "duckduckgo"
    assert get_search_provider(Settings(search_backend="searxng")).name == "searxng"
