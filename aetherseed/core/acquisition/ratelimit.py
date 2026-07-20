"""Politeness controls: per-host delays and bounded global concurrency.

The :class:`RateLimiter` enforces a minimum interval between requests to the
*same host* (so we never hammer one server) while a global
:class:`asyncio.Semaphore` bounds total in-flight requests. Jitter is added to
avoid lock-step request bursts.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict


class RateLimiter:
    """Async per-host politeness limiter with a global concurrency cap.

    Parameters
    ----------
    max_concurrency:
        Maximum number of simultaneously in-flight requests across all hosts.
    polite_delay_ms:
        Minimum spacing between two requests to the same host.
    jitter_ratio:
        Fractional random jitter added to each delay (0.2 => up to +20%).
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        polite_delay_ms: int = 750,
        jitter_ratio: float = 0.2,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self._delay = polite_delay_ms / 1000.0
        self._jitter = jitter_ratio
        self._next_allowed: dict[str, float] = defaultdict(float)
        self._host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, host: str) -> None:
        """Block until it is polite to issue a request to ``host``."""
        await self._sem.acquire()
        lock = self._host_locks[host]
        async with lock:
            now = time.monotonic()
            wait = self._next_allowed[host] - now
            if wait > 0:
                await asyncio.sleep(wait)
            delay = self._delay * (1 + random.random() * self._jitter)
            self._next_allowed[host] = time.monotonic() + delay

    def release(self) -> None:
        """Release the global concurrency slot."""
        self._sem.release()

    async def __aenter__(self) -> RateLimiter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None
