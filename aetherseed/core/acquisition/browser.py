"""JS-rendering fetcher and screenshotter (Playwright) — optional, graceful.

Playwright is an optional dependency (``pip install '.[browser]' && playwright
install chromium``). When it is absent, :meth:`PlaywrightFetcher.available`
returns ``False`` and callers fall back to the static fetcher, so the platform
never hard-fails for lack of a browser.

Stealth measures applied: realistic UA + viewport, ``navigator.webdriver``
masking, locale/timezone hints, and proxy support. SSRF validation runs before
every navigation, exactly as in the static path.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from aetherseed.config import Settings, get_settings
from aetherseed.core.acquisition.security import resolve_and_validate
from aetherseed.core.interfaces import FetchResult
from aetherseed.errors import BackendUnavailableError, PolicyError
from aetherseed.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

log = get_logger(__name__)

_STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-ZA', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""


def playwright_available() -> bool:
    """Whether the Playwright package is importable."""
    try:
        import playwright.async_api  # noqa: F401

        return True
    except ImportError:
        return False


class PlaywrightFetcher:
    """JS-rendering fetcher + screenshotter implementing the Fetcher protocol."""

    name = "playwright"

    def __init__(self, settings: Settings | None = None, *, stealth: bool = True) -> None:
        self._settings = settings or get_settings()
        self._stealth = stealth
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    @staticmethod
    def available() -> bool:
        return playwright_available()

    async def _ensure_browser(self) -> Browser:
        if not self.available():
            raise BackendUnavailableError(
                "Playwright not installed; run: pip install '.[browser]' && playwright install chromium"
            )
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {"headless": True}
            if self._settings.acq_proxy_url:
                launch_kwargs["proxy"] = {"server": self._settings.acq_proxy_url}
            self._browser = await self._pw.chromium.launch(**launch_kwargs)
        return self._browser

    async def _new_context(self) -> Any:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=self._settings.acq_user_agent,
            viewport={"width": 1366, "height": 900},
            locale="en-ZA",
            timezone_id="Africa/Johannesburg",
        )
        if self._stealth:
            await context.add_init_script(_STEALTH_INIT_JS)
        return context

    async def fetch(self, url: str, *, render: bool = True) -> FetchResult:
        """Navigate to ``url`` with a real browser and return rendered HTML."""
        started = time.monotonic()
        try:
            resolve_and_validate(url, self._settings)
        except PolicyError as exc:
            return FetchResult(
                url=url, final_url=url, status_code=0, ok=False, error=str(exc)
            )

        context = await self._new_context()
        try:
            page = await context.new_page()
            response = await page.goto(
                url, wait_until="networkidle", timeout=self._settings.acq_request_timeout_s * 1000
            )
            content = (await page.content()).encode("utf-8")
            status = response.status if response else 0
            final_url = page.url
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=status,
                content=content,
                content_type="text/html; charset=utf-8",
                elapsed_ms=(time.monotonic() - started) * 1000,
                ok=status < 400,
                error=None if status < 400 else f"HTTP {status}",
                rendered=True,
                content_hash=hashlib.sha256(content).hexdigest(),
            )
        except Exception as exc:
            log.warning("browser.fetch_failed", url=url, error=str(exc))
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                elapsed_ms=(time.monotonic() - started) * 1000,
                ok=False,
                error=str(exc),
            )
        finally:
            await context.close()

    async def screenshot(self, url: str, *, full_page: bool = True) -> bytes:
        """Capture a PNG screenshot of ``url``.

        Raises
        ------
        BackendUnavailableError
            If Playwright is not installed.
        PolicyError
            If the URL fails SSRF validation.
        """
        resolve_and_validate(url, self._settings)
        context = await self._new_context()
        try:
            page = await context.new_page()
            await page.goto(
                url, wait_until="networkidle", timeout=self._settings.acq_request_timeout_s * 1000
            )
            png: bytes = await page.screenshot(full_page=full_page, type="png")
            return png
        finally:
            await context.close()

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
