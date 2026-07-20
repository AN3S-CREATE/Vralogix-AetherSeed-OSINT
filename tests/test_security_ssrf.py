"""SSRF egress-policy tests."""

from __future__ import annotations

import pytest
from aetherseed.config import Settings
from aetherseed.core.acquisition.security import resolve_and_validate
from aetherseed.errors import SSRFError


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://[::1]/",
    ],
)
def test_blocks_non_public_targets(url: str) -> None:
    with pytest.raises(SSRFError):
        resolve_and_validate(url, Settings(acq_egress_allowlist=""))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://x/"])
def test_blocks_disallowed_schemes(url: str) -> None:
    with pytest.raises(SSRFError):
        resolve_and_validate(url, Settings())


def test_allowlist_opts_in_private_target() -> None:
    # Explicitly allowlisting loopback permits it (internal-crawl / test use).
    target = resolve_and_validate("http://127.0.0.1/", Settings(acq_egress_allowlist="127.0.0.1"))
    assert target.host == "127.0.0.1"


def test_missing_host_rejected() -> None:
    with pytest.raises(SSRFError):
        resolve_and_validate("http:///nohost", Settings())
