"""Static HTTP fetcher (httpx) — the default acquisition backend.

Wires together the safety layer (SSRF validation, robots.txt, rate limiting) and
resilient retries (tenacity, transient errors only) behind the
:class:`~aetherseed.core.interfaces.Fetcher` protocol. Produces a
:class:`~aetherseed.core.interfaces.FetchResult` for every attempt — success or
a structured failure — never raising past the per-item boundary for expected
network errors.
"""

from __future__ import annotations

import hashlib
import time
from types import TracebackType
from urllib.parse import urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from aetherseed.config import Settings, get_settings
from aetherseed.core.acquisition.ratelimit import RateLimiter
from aetherseed.core.acquisition.robots import RobotsChecker
from aetherseed.core.acquisition.security import resolve_and_validate
from aetherseed.core.interfaces import FetchResult
from aetherseed.errors import FetchError, PolicyError, RobotsDisallowedError
from aetherseed.logging import get_logger

log = get_logger(__name__)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpxFetcher:
    """Async static fetcher implementing the :class:`Fetcher` protocol."""

    name = "httpx"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
        respect_robots: bool | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._settings = settings or get_settings()
        self._max_attempts = max_attempts
        self._limiter = rate_limiter or RateLimiter(
            max_concurrency=self._settings.acq_max_concurrency,
            polite_delay_ms=self._settings.acq_polite_delay_ms,
        )
        self._robots = RobotsChecker(
            user_agent=self._settings.acq_user_agent,
            respect=self._settings.acq_respect_robots if respect_robots is None else respect_robots,
        )
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._settings.acq_request_timeout_s,
            headers={"User-Agent": self._settings.acq_user_agent},
            proxy=self._settings.acq_proxy_url,
            limits=httpx.Limits(max_connections=self._settings.acq_max_concurrency),
        )

    async def fetch(self, url: str, *, render: bool = False) -> FetchResult:
        """Fetch ``url``. ``render`` is ignored (use PlaywrightFetcher for JS)."""
        started = time.monotonic()
        host = urlparse(url).hostname or ""
        try:
            resolve_and_validate(url, self._settings)  # SSRF choke point
            if not await self._robots.allowed(url, self._client):
                raise RobotsDisallowedError(
                    "blocked by robots.txt", context={"url": url}
                )
        except PolicyError as exc:
            return self._failure(url, started, exc, status=0)

        try:
            resp = await self._fetch_with_retry(url, host)
        except (httpx.HTTPError, _RetryableStatusError) as exc:
            return self._failure(
                url,
                started,
                FetchError(str(exc), context={"url": url, "exc": type(exc).__name__}),
                status=0,
            )

        content = resp.content
        return FetchResult(
            url=url,
            final_url=str(resp.url),
            status_code=resp.status_code,
            content=content,
            content_type=resp.headers.get("content-type"),
            headers={k.lower(): v for k, v in resp.headers.items()},
            elapsed_ms=(time.monotonic() - started) * 1000,
            ok=resp.status_code < 400,
            error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
            content_hash=hashlib.sha256(content).hexdigest(),
        )

    async def _fetch_with_retry(self, url: str, host: str) -> httpx.Response:
        # Acquire/release are paired inside each attempt so the concurrency slot
        # is freed during backoff. Do not also release in fetch() — that double-
        # frees the semaphore when retries are exhausted.
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=0.5, max=10.0),
            retry=retry_if_exception_type((httpx.TransportError, _RetryableStatusError)),
            reraise=True,
        ):
            with attempt:
                await self._limiter.acquire(host)
                try:
                    resp = await self._client.get(url)
                except BaseException:
                    self._limiter.release()
                    raise
                if resp.status_code in _RETRYABLE_STATUS:
                    self._limiter.release()
                    raise _RetryableStatusError(resp.status_code)
                self._limiter.release()
                return resp
        raise FetchError("unreachable", context={"url": url})  # pragma: no cover

    def _failure(
        self, url: str, started: float, exc: Exception, *, status: int
    ) -> FetchResult:
        log.warning("fetch.failed", url=url, error=str(exc), type=type(exc).__name__)
        return FetchResult(
            url=url,
            final_url=url,
            status_code=status,
            elapsed_ms=(time.monotonic() - started) * 1000,
            ok=False,
            error=str(exc),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpxFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


class _RetryableStatusError(Exception):
    """Internal marker so tenacity retries on retryable HTTP status codes."""

    def __init__(self, status: int) -> None:
        super().__init__(f"retryable status {status}")
        self.status = status
