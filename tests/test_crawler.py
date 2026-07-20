"""Crawler scope, depth, dedup, and concurrency tests (offline)."""

from __future__ import annotations

from aetherseed.core.acquisition.crawler import Crawler, normalize_url, registrable_domain
from aetherseed.core.acquisition.extract import HtmlExtractor

from tests.fakes import FakeFetcher

PAGES = {
    "http://example.com/": "<a href='/a'>a</a><a href='/b'>b</a><a href='http://other.com/x'>ext</a>",
    "http://example.com/a": "<a href='/c'>c</a>",
    "http://example.com/b": "leaf b",
    "http://example.com/c": "deep c",
    "http://example.com/dup1": "identical",
    "http://example.com/dup2": "identical",
}


def test_normalize_and_domain() -> None:
    assert normalize_url("http://x.com/a#frag") == "http://x.com/a"
    assert registrable_domain("http://sub.acme.co.za/p") == "acme.co.za"


async def test_depth_and_domain_scope() -> None:
    crawler = Crawler(FakeFetcher(PAGES), HtmlExtractor(), max_depth=1, max_pages=50, workers=4)
    ok = [o.url async for o in crawler.crawl(["http://example.com/"]) if o.ok]
    assert "http://example.com/a" in ok
    assert "http://example.com/b" in ok
    assert "http://example.com/c" not in ok  # depth 2 > max_depth 1
    assert all("other.com" not in u for u in ok)  # off-domain excluded


async def test_max_pages_enforced() -> None:
    crawler = Crawler(FakeFetcher(PAGES), HtmlExtractor(), max_depth=3, max_pages=2, workers=2)
    count = 0
    async for _ in crawler.crawl(["http://example.com/"]):
        count += 1
    assert count <= 2


async def test_content_hash_dedup() -> None:
    crawler = Crawler(FakeFetcher(PAGES), HtmlExtractor(), max_depth=1, max_pages=50, workers=1)
    outcomes = [o async for o in crawler.crawl(["http://example.com/dup1", "http://example.com/dup2"])]
    skipped = [o for o in outcomes if o.skipped]
    assert len(skipped) == 1  # second identical page skipped
