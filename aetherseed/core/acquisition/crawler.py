"""Priority-queue web crawler with per-item fault isolation.

Features
--------
* **Priority frontier** — links are scored (a pluggable ``link_scorer`` lets the
  AI engine inject relevance scores) and the highest-priority URLs are fetched
  first. This is the "walking" mode from the spec.
* **Bounded, concurrent worker pool** — N workers drain a shared
  :class:`asyncio.PriorityQueue`; global politeness/concurrency is enforced by
  the fetcher's rate limiter.
* **Scope controls** — configurable ``max_depth``, ``max_pages``, same-domain
  restriction, and an explicit host allowlist.
* **Deduplication** — by normalised URL and by content hash (shared sets can be
  seeded from a prior run for resumability).
* **Fault isolation** — a failed fetch/parse is yielded as a failed
  :class:`CrawlOutcome`; it never aborts the crawl.

The crawler yields :class:`CrawlOutcome` objects as they complete so the caller
can process and checkpoint incrementally.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urlparse

import tldextract

from aetherseed.core.interfaces import ContentExtractor, ExtractedContent, Fetcher, FetchResult
from aetherseed.logging import get_logger

log = get_logger(__name__)

LinkScorer = Callable[[str, str, int], float]


def registrable_domain(url: str) -> str:
    """Return the registrable domain (eTLD+1) of a URL, e.g. ``sub.acme.co.za`` -> ``acme.co.za``."""
    ext = tldextract.extract(url)
    if not ext.domain:
        return urlparse(url).hostname or ""
    return ".".join(part for part in (ext.domain, ext.suffix) if part)


def normalize_url(url: str) -> str:
    """Strip the fragment and normalise for dedup purposes."""
    return urldefrag(url.strip()).url.rstrip("/") or url.strip()


def _default_scorer(url: str, anchor: str, depth: int) -> float:
    """Cheap default relevance: shallower is better, keyword hints boost."""
    score = max(0.1, 1.0 - depth * 0.25)
    hints = ("about", "team", "director", "owner", "contact", "company", "board", "register")
    blob = f"{url} {anchor}".lower()
    if any(h in blob for h in hints):
        score += 0.2
    return min(score, 1.0)


@dataclass(order=True)
class _QueueItem:
    sort_key: tuple[float, int]
    url: str = field(compare=False)
    depth: int = field(compare=False)
    anchor: str = field(compare=False, default="")
    parent: str | None = field(compare=False, default=None)


@dataclass(slots=True)
class CrawlOutcome:
    """One processed URL: a parsed page, a skip, or a structured failure."""

    url: str
    depth: int
    ok: bool
    result: FetchResult | None = None
    content: ExtractedContent | None = None
    error: str | None = None
    skipped: bool = False  # e.g. duplicate content — not a failure


class Crawler:
    """Configurable, concurrent, priority-driven crawler."""

    def __init__(
        self,
        fetcher: Fetcher,
        extractor: ContentExtractor,
        *,
        max_depth: int = 2,
        max_pages: int = 200,
        workers: int = 8,
        same_domain_only: bool = True,
        allowed_hosts: set[str] | None = None,
        render: bool = False,
        link_scorer: LinkScorer | None = None,
        seen_urls: set[str] | None = None,
        seen_hashes: set[str] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._extractor = extractor
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.workers = max(1, workers)
        self.same_domain_only = same_domain_only
        self.allowed_hosts = allowed_hosts or set()
        self.render = render
        self._score = link_scorer or _default_scorer
        self._seen_urls = seen_urls if seen_urls is not None else set()
        self._seen_hashes = seen_hashes if seen_hashes is not None else set()
        self._seed_domains: set[str] = set()
        self._admitted = 0
        self._counter = itertools.count()

    def _in_scope(self, url: str, depth: int) -> bool:
        if depth > self.max_depth:
            return False
        host = urlparse(url).hostname
        if not host:
            return False
        if self.allowed_hosts and host not in self.allowed_hosts:
            return False
        return not (
            self.same_domain_only
            and self._seed_domains
            and registrable_domain(url) not in self._seed_domains
        )

    def _admit(self, url: str, depth: int) -> bool:
        norm = normalize_url(url)
        if norm in self._seen_urls:
            return False
        if not self._in_scope(url, depth):
            return False
        if self._admitted >= self.max_pages:
            return False
        self._seen_urls.add(norm)
        self._admitted += 1
        return True

    async def crawl(self, seeds: list[str]) -> AsyncIterator[CrawlOutcome]:
        """Crawl from ``seeds``, yielding :class:`CrawlOutcome` as pages complete."""
        for seed in seeds:
            self._seed_domains.add(registrable_domain(seed))

        queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        output: asyncio.Queue[CrawlOutcome | None] = asyncio.Queue()

        for seed in seeds:
            if self._admit(seed, 0):
                queue.put_nowait(_QueueItem((-1.0, next(self._counter)), seed, 0, "", None))

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    outcome = await self._process(item)
                    await output.put(outcome)
                    if outcome.ok and outcome.content and item.depth < self.max_depth:
                        self._enqueue_children(queue, outcome.content, item.depth + 1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("crawl.worker_error", url=item.url, error=str(exc))
                    await output.put(
                        CrawlOutcome(url=item.url, depth=item.depth, ok=False, error=str(exc))
                    )
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.workers)]

        async def _drain() -> None:
            await queue.join()
            await output.put(None)  # sentinel

        drainer = asyncio.create_task(_drain())

        try:
            while True:
                outcome = await output.get()
                if outcome is None:
                    break
                yield outcome
        finally:
            for w in workers:
                w.cancel()
            drainer.cancel()
            await asyncio.gather(*workers, drainer, return_exceptions=True)

    async def _process(self, item: _QueueItem) -> CrawlOutcome:
        result = await self._fetcher.fetch(item.url, render=self.render)
        if not result.ok:
            return CrawlOutcome(
                url=item.url, depth=item.depth, ok=False, result=result, error=result.error
            )
        if result.content_hash and result.content_hash in self._seen_hashes:
            return CrawlOutcome(
                url=item.url, depth=item.depth, ok=False, skipped=True,
                result=result, error="duplicate content",
            )
        if result.content_hash:
            self._seen_hashes.add(result.content_hash)
        content = self._extractor.extract(result)
        return CrawlOutcome(url=item.url, depth=item.depth, ok=True, result=result, content=content)

    def _enqueue_children(
        self, queue: asyncio.PriorityQueue[_QueueItem], content: ExtractedContent, depth: int
    ) -> None:
        for link in content.links:
            if self._admitted >= self.max_pages:
                break
            if self._admit(link, depth):
                score = self._score(link, "", depth)
                queue.put_nowait(_QueueItem((-score, next(self._counter)), link, depth, "", content.url))

    @property
    def pages_admitted(self) -> int:
        return self._admitted
