"""Content-addressable asset store (evidence locker).

Screenshots and downloads are written under ``<data_dir>/assets/<ab>/<sha256>``
where ``<ab>`` is the first two hex chars of the hash (fan-out to avoid huge
directories). Content addressing gives free deduplication and tamper-evidence:
the filename *is* the SHA-256, so any modification is detectable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from aetherseed.config import Settings, get_settings
from aetherseed.schemas import AssetRecord

_EXT_BY_KIND = {"screenshot": ".png", "pdf": ".pdf", "html": ".html"}


class FilesystemAssetStore:
    """Local filesystem implementation of the :class:`AssetStore` protocol."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.root = self._settings.data_dir / "assets"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, sha256: str, kind: str, content_type: str | None) -> Path:
        ext = _EXT_BY_KIND.get(kind, self._ext_from_content_type(content_type))
        shard = self.root / sha256[:2]
        shard.mkdir(parents=True, exist_ok=True)
        return shard / f"{sha256}{ext}"

    @staticmethod
    def _ext_from_content_type(content_type: str | None) -> str:
        if not content_type:
            return ".bin"
        mapping = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "text/html": ".html",
            "application/json": ".json",
            "application/zip": ".zip",
        }
        return mapping.get(content_type.split(";")[0].strip().lower(), ".bin")

    def put(
        self,
        data: bytes,
        *,
        kind: str = "other",
        content_type: str | None = None,
        source_url: str | None = None,
    ) -> AssetRecord:
        """Store ``data`` and return its :class:`AssetRecord`.

        Idempotent: identical bytes map to the same path and are not rewritten.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        path = self._path_for(sha256, kind, content_type)
        if not path.exists():
            path.write_bytes(data)
        return AssetRecord(
            kind=kind,
            path=str(path),
            content_type=content_type,
            sha256=sha256,
            size_bytes=len(data),
            source_url=source_url,
        )

    def get(self, sha256: str) -> bytes:
        """Retrieve stored bytes by content hash.

        Raises
        ------
        FileNotFoundError
            If no asset with that hash exists.
        """
        shard = self.root / sha256[:2]
        for candidate in shard.glob(f"{sha256}*"):
            return candidate.read_bytes()
        raise FileNotFoundError(f"asset {sha256} not found")

    def verify(self, record: AssetRecord) -> bool:
        """Recompute the hash and confirm the stored asset is intact."""
        path = Path(record.path)
        if not path.exists():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == record.sha256
