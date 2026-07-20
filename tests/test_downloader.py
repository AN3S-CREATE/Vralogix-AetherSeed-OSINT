"""Asset downloader tests (respx, no network)."""

from __future__ import annotations

import httpx
import pytest
import respx
from aetherseed.core.acquisition import downloader as dl_mod
from aetherseed.core.acquisition.downloader import AssetDownloader
from aetherseed.errors import PolicyError


@pytest.fixture
def _no_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl_mod, "resolve_and_validate", lambda url, s: None)


@respx.mock
async def test_download_ok(_no_ssrf: None, env) -> None:
    respx.get("http://x/f.pdf").mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4 data", headers={"content-type": "application/pdf"}
        )
    )
    rec = await AssetDownloader(env).download("http://x/f.pdf")
    assert rec.kind == "pdf" and rec.size_bytes > 0 and rec.sha256


@respx.mock
async def test_size_cap_enforced(_no_ssrf: None, env) -> None:
    env.acq_max_asset_bytes = 10
    respx.get("http://x/big").mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-type": "application/octet-stream", "content-length": "100"},
        )
    )
    with pytest.raises(PolicyError, match="size cap"):
        await AssetDownloader(env).download("http://x/big")


@respx.mock
async def test_denied_content_type(_no_ssrf: None, env) -> None:
    respx.get("http://x/evil.exe").mock(
        return_value=httpx.Response(200, content=b"MZ", headers={"content-type": "application/x-msdownload"})
    )
    with pytest.raises(PolicyError, match="denied"):
        await AssetDownloader(env).download("http://x/evil.exe")


@respx.mock
async def test_scan_hook_can_reject(_no_ssrf: None, env) -> None:
    respx.get("http://x/doc").mock(
        return_value=httpx.Response(200, content=b"data", headers={"content-type": "text/plain"})
    )
    downloader = AssetDownloader(env, scan_hook=lambda data, ct: False)
    with pytest.raises(PolicyError, match="scan hook"):
        await downloader.download("http://x/doc")
