"""Concrete ``VectorIndex`` backends and their factory.

Three implementations satisfy the ``VectorIndex`` Protocol in
:mod:`aetherseed.core.interfaces`:

* :class:`InMemoryVectorIndex` — the offline default. Wraps
  :class:`~aetherseed.core.rag.retrieval.HybridRetriever` (BM25 + RRF). Zero
  external dependencies; deterministic.
* :class:`ChromaVectorIndex` — dense semantic search backed by ChromaDB (the
  ``ai`` extra). Uses an ephemeral in-memory client by default so a single run's
  corpus stays isolated; pass ``persist_dir`` for durable storage.
* :class:`NullVectorIndex` — disabled retrieval (returns nothing).

:func:`get_vector_index` picks one from settings and **degrades gracefully**:
if ``chroma`` is requested but the library is missing, it falls back to the
in-memory index and logs a warning rather than failing the run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aetherseed.config import Settings, get_settings
from aetherseed.core.rag.retrieval import Document, HybridRetriever
from aetherseed.logging import get_logger

log = get_logger(__name__)


class InMemoryVectorIndex:
    """Offline, dependency-free lexical index (the default backend)."""

    name = "memory"

    def __init__(self, *, cross_encoder_model: str | None = None) -> None:
        self._retriever = HybridRetriever(cross_encoder_model=cross_encoder_model)

    def __len__(self) -> int:
        return len(self._retriever)

    def add(
        self,
        ids: Sequence[str],
        texts: Sequence[str],
        metadata: Sequence[dict[str, Any]],
    ) -> None:
        """Index ``texts`` under ``ids`` with parallel ``metadata``."""
        if not (len(ids) == len(texts) == len(metadata)):
            raise ValueError("ids, texts, and metadata must be the same length")
        self._retriever.add(
            [
                Document(id=i, text=t, metadata=dict(m))
                for i, t, m in zip(ids, texts, metadata, strict=True)
                if t and t.strip()
            ]
        )

    def query(self, text: str, *, k: int = 5) -> list[dict[str, Any]]:
        """Return up to ``k`` nearest documents as serialisable dicts."""
        return [
            {
                "id": hit.id,
                "text": hit.text,
                "snippet": hit.snippet(),
                "score": hit.score,
                "metadata": hit.metadata,
            }
            for hit in self._retriever.query(text, k=k)
        ]


class NullVectorIndex:
    """Disabled retrieval: indexes nothing, returns nothing."""

    name = "none"

    def __len__(self) -> int:
        return 0

    def add(
        self,
        ids: Sequence[str],
        texts: Sequence[str],
        metadata: Sequence[dict[str, Any]],
    ) -> None:
        return None

    def query(self, text: str, *, k: int = 5) -> list[dict[str, Any]]:
        return []


class ChromaVectorIndex:
    """Dense semantic index backed by ChromaDB (optional ``ai`` extra).

    Falls back to no-op behaviour only if construction succeeds but the backend
    later misbehaves; construction itself raises if ``chromadb`` is unavailable,
    which :func:`get_vector_index` catches to degrade to the in-memory index.
    """

    name = "chroma"

    def __init__(
        self, *, persist_dir: str | None = None, collection: str = "aetherseed"
    ) -> None:
        import chromadb  # local import: keeps ``chromadb`` an optional dependency

        self._client = (
            chromadb.PersistentClient(path=persist_dir)
            if persist_dir
            else chromadb.EphemeralClient()
        )
        self._col = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    def __len__(self) -> int:
        return int(self._col.count())

    def add(
        self,
        ids: Sequence[str],
        texts: Sequence[str],
        metadata: Sequence[dict[str, Any]],
    ) -> None:
        if not (len(ids) == len(texts) == len(metadata)):
            raise ValueError("ids, texts, and metadata must be the same length")
        keep = [(i, t, m) for i, t, m in zip(ids, texts, metadata, strict=True) if t and t.strip()]
        if not keep:
            return
        self._col.upsert(
            ids=[i for i, _, _ in keep],
            documents=[t for _, t, _ in keep],
            metadatas=[dict(m) or {"_": ""} for _, _, m in keep],
        )

    def query(self, text: str, *, k: int = 5) -> list[dict[str, Any]]:
        if not text.strip() or self._col.count() == 0:
            return []
        res = self._col.query(query_texts=[text], n_results=k)
        docs = (res.get("documents") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out: list[dict[str, Any]] = []
        for i, doc, meta, dist in zip(ids, docs, metas, dists, strict=False):
            flat = " ".join((doc or "").split())
            out.append(
                {
                    "id": i,
                    "text": doc,
                    "snippet": flat if len(flat) <= 320 else flat[:319].rstrip() + "…",
                    "score": round(1.0 - float(dist), 6),  # cosine distance -> similarity
                    "metadata": dict(meta or {}),
                }
            )
        return out


def get_vector_index(settings: Settings | None = None) -> Any:
    """Return the configured vector index, honouring local-first degradation.

    Resolution:
    1. ``vector_backend == "none"`` -> :class:`NullVectorIndex`.
    2. ``vector_backend == "chroma"`` -> :class:`ChromaVectorIndex` if importable,
       else :class:`InMemoryVectorIndex` (logged).
    3. Otherwise -> :class:`InMemoryVectorIndex`.
    """
    s = settings or get_settings()
    if not s.rag_enabled or s.vector_backend == "none":
        return NullVectorIndex()
    if s.vector_backend == "chroma":
        try:
            return ChromaVectorIndex()
        except Exception as exc:  # ImportError or backend init failure
            log.warning("rag.chroma_unavailable", error=str(exc), fallback="memory")
    return InMemoryVectorIndex()
