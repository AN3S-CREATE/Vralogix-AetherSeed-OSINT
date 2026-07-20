"""Shared pytest fixtures.

Every test runs against an isolated temp SQLite DB and data directory so state
never leaks between tests. The AI backend defaults to ``null`` (deterministic
heuristics) and politeness delays are zeroed for speed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from aetherseed.config import Settings, get_settings
from aetherseed.core.storage import db as db_mod


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Isolated settings + database + data dir for a single test."""
    db_file = (tmp_path / "db.sqlite3").as_posix()
    monkeypatch.setenv("AETHERSEED_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AETHERSEED_DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("AETHERSEED_AI_BACKEND", "null")
    monkeypatch.setenv("AETHERSEED_ACQ_POLITE_DELAY_MS", "0")
    monkeypatch.setenv("AETHERSEED_SEED_REQUIRE_APPROVAL", "false")
    monkeypatch.setenv("AETHERSEED_ACQ_EGRESS_ALLOWLIST", "")

    get_settings.cache_clear()
    db_mod.reset_engine()
    settings = get_settings()
    settings.ensure_dirs()
    db_mod.init_db(settings)

    yield settings

    db_mod.reset_engine()
    get_settings.cache_clear()
