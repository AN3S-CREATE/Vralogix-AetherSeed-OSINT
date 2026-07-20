"""robots.txt compliance with an in-process cache.

Fetches and caches ``robots.txt`` per origin using the standard library
:class:`urllib.robotparser.RobotFileParser`. When a run sets ``respect_robots``
false (an explicit, audited override) the checker allows everything.
"""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from aetherseed.logging import get_logger

log = get_logger(__name__)


class RobotsChecker:
    """Caches robots.txt decisions per origin.

    Parameters
    ----------
    user_agent:
        The UA string tested against robots directives.
    respect:
        When ``False`` all URLs are permitted (explicit per-run override).
    """

    def __init__(self, *, user_agent: str, respect: bool = True, timeout: float = 10.0) -> None:
        self.user_agent = user_agent
        self.respect = respect
        self.timeout = timeout
        self._cache: dict[str, RobotFileParser | None] = {}

    def _origin(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    async def _load(self, origin: str, client: httpx.AsyncClient) -> RobotFileParser | None:
        if origin in self._cache:
            return self._cache[origin]
        parser = RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        result: RobotFileParser | None = parser
        try:
            resp = await client.get(robots_url, timeout=self.timeout)
            if resp.status_code >= 400:
                result = None  # no robots.txt => allow all
            else:
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            result = None  # network failure => fail open (do not block the crawl)
        self._cache[origin] = result
        return result

    async def allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        """Whether ``url`` may be fetched under the current policy."""
        if not self.respect:
            return True
        parser = await self._load(self._origin(url), client)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """Return the robots-declared crawl delay for ``url``'s origin, if any."""
        parser = self._cache.get(self._origin(url))
        if parser is None:
            return None
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
