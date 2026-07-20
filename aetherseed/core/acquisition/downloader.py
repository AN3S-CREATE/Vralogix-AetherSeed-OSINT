"""Asset downloader with content-type validation, size caps, and a scan hook.

Downloads binary assets (PDFs, images, documents, archives) safely:

* SSRF validation before the request.
* Streamed download with a hard byte cap (never buffer an unbounded response).
* Content-type allow/deny lists (executables are blocked by default).
* A pluggable virus-scan hook — provide a callable and it runs on the bytes
  before they are persisted; returning ``False`` rejects the asset.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from aetherseed.config import Settings, get_settings
from aetherseed.core.acquisition.security import resolve_and_validate
from aetherseed.core.storage.asset_store import FilesystemAssetStore
from aetherseed.errors import FetchError, PolicyError
from aetherseed.logging import get_logger
from aetherseed.schemas import AssetRecord

log = get_logger(__name__)

ScanHook = Callable[[bytes, str | None], bool]

# Executables / scripts we refuse to persist by default.
_DENY_CONTENT_TYPES = {
    "application/x-msdownload",
    "application/x-dosexec",
    "application/x-executable",
    "application/x-sh",
    "application/x-msdos-program",
}


def _kind_for(content_type: str | None) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf"
    if ct.startswith("image/"):
        return "download"
    if ct.startswith("text/html"):
        return "html"
    return "download"


class AssetDownloader:
    """Downloads and stores binary assets safely."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        asset_store: FilesystemAssetStore | None = None,
        scan_hook: ScanHook | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._store = asset_store or FilesystemAssetStore(self._settings)
        self._scan = scan_hook

    async def download(self, url: str, *, client: httpx.AsyncClient | None = None) -> AssetRecord:
        """Download ``url`` and persist it, returning its :class:`AssetRecord`.

        Raises
        ------
        PolicyError
            SSRF block, denied content-type, oversized asset, or scan rejection.
        FetchError
            Network failure.
        """
        resolve_and_validate(url, self._settings)
        owns_client = client is None
        client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._settings.acq_request_timeout_s,
            headers={"User-Agent": self._settings.acq_user_agent},
            proxy=self._settings.acq_proxy_url,
        )
        try:
            data, content_type = await self._stream(client, url)
        except httpx.HTTPError as exc:
            raise FetchError(str(exc), context={"url": url}) from exc
        finally:
            if owns_client:
                await client.aclose()

        ct_main = (content_type or "").split(";")[0].strip().lower()
        if ct_main in _DENY_CONTENT_TYPES:
            raise PolicyError(
                f"content-type {ct_main!r} is denied", context={"url": url, "content_type": ct_main}
            )

        if self._scan is not None and not self._scan(data, content_type):
            raise PolicyError("asset rejected by scan hook", context={"url": url})

        record = self._store.put(
            data, kind=_kind_for(content_type), content_type=content_type, source_url=url
        )
        log.info("asset.downloaded", url=url, sha256=record.sha256, size=record.size_bytes)
        return record

    async def _stream(self, client: httpx.AsyncClient, url: str) -> tuple[bytes, str | None]:
        max_bytes = self._settings.acq_max_asset_bytes
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type")
            declared = resp.headers.get("content-length")
            if declared and int(declared) > max_bytes:
                raise PolicyError(
                    f"asset exceeds size cap ({declared} > {max_bytes})",
                    context={"url": url, "content_length": declared},
                )
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PolicyError(
                        f"asset exceeds size cap while streaming (> {max_bytes})",
                        context={"url": url},
                    )
                chunks.append(chunk)
        return b"".join(chunks), content_type
