"""HttpxFetcher tests using respx (no real network)."""

from __future__ import annotations

import httpx
import pytest
import respx
from aetherseed.config import Settings
from aetherseed.core.acquisition import fetcher as fetcher_mod
from aetherseed.core.acquisition.fetcher import HttpxFetcher


@pytest.fixture
def _no_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher_mod, "resolve_and_validate", lambda url, s: None)


def _fetcher() -> HttpxFetcher:
    return HttpxFetcher(Settings(acq_respect_robots=False, acq_polite_delay_ms=0), max_attempts=3)


@respx.mock
async def test_fetch_ok(_no_ssrf: None) -> None:
    respx.get("http://example.com/").mock(
        return_value=httpx.Response(200, html="<h1>ok</h1>", headers={"content-type": "text/html"})
    )
    f = _fetcher()
    res = await f.fetch("http://example.com/")
    await f.aclose()
    assert res.ok and res.status_code == 200
    assert b"ok" in res.content
    assert res.content_hash


@respx.mock
async def test_retries_on_429_then_succeeds(_no_ssrf: None) -> None:
    respx.get("http://example.com/").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, text="done")]
    )
    f = _fetcher()
    res = await f.fetch("http://example.com/")
    await f.aclose()
    assert res.ok and b"done" in res.content


@respx.mock
async def test_404_is_not_ok_but_not_error(_no_ssrf: None) -> None:
    respx.get("http://example.com/missing").mock(return_value=httpx.Response(404))
    f = _fetcher()
    res = await f.fetch("http://example.com/missing")
    await f.aclose()
    assert res.ok is False and res.status_code == 404
