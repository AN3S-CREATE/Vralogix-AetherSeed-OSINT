"""Append-only JSONL audit log.

Every material decision (fetch, extract, seed proposal, approval, policy block,
error) is appended as one JSON line to ``<data_dir>/audit/<run_id>.jsonl``. The
log is write-once per event and hash-chained: each record carries the SHA-256 of
the previous record, so tampering with any earlier line is detectable. This is
the backbone of the platform's auditability guarantee.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aetherseed.config import Settings, get_settings

_GENESIS = "0" * 64


class AuditLog:
    """Hash-chained, append-only audit log for a single run."""

    def __init__(self, run_id: str, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.run_id = run_id
        self.dir = self._settings.data_dir / "audit"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self._lock = threading.Lock()
        self._last_hash = self._read_last_hash()

    @property
    def ref(self) -> str:
        """Reference string stored on the run record."""
        return str(self.path)

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return _GENESIS
        last = _GENESIS
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line).get("_hash", last)
                    except json.JSONDecodeError:
                        continue
        return last

    def emit(self, event: str, **fields: Any) -> str:
        """Append an event; returns the record hash.

        Parameters
        ----------
        event:
            Short event name, e.g. ``"page.fetched"`` or ``"seed.proposed"``.
        **fields:
            Structured, JSON-serialisable context. Do not log secrets or raw PII
            when redaction is enabled upstream.
        """
        with self._lock:
            record: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "event": event,
                "_prev": self._last_hash,
                **fields,
            }
            payload = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
            digest = hashlib.sha256(f"{self._last_hash}{payload}".encode()).hexdigest()
            record["_hash"] = digest
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
            self._last_hash = digest
            return digest

    def verify_chain(self) -> bool:
        """Recompute the hash chain end-to-end; ``True`` if intact."""
        prev = _GENESIS
        if not self.path.exists():
            return True
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                stored = record.pop("_hash", None)
                if record.get("_prev") != prev:
                    return False
                payload = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
                digest = hashlib.sha256(f"{prev}{payload}".encode()).hexdigest()
                if digest != stored:
                    return False
                prev = digest
        return True

    def read_all(self) -> list[dict[str, Any]]:
        """Return every audit record as a list of dicts (for reports)."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def audit_path_for(run_id: str, settings: Settings | None = None) -> Path:
    """Return the audit-log path for a run without opening it."""
    s = settings or get_settings()
    return s.data_dir / "audit" / f"{run_id}.jsonl"
