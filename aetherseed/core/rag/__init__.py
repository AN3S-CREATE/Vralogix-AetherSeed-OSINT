"""Hybrid retrieval-augmented generation (RAG) over the collected corpus.

This package turns the raw text of fetched pages into a searchable corpus so the
platform can *ground* its outputs — attaching supporting evidence snippets to
leads and (optionally) feeding retrieved context to the AI engine.

Design goals mirror the rest of AetherSeed:

* **Local-first / offline.** The default :class:`~aetherseed.core.rag.index.InMemoryVectorIndex`
  is pure Python (Okapi BM25 + Reciprocal Rank Fusion) and needs no model,
  service, or extra dependency. It works with an empty ``.env``.
* **Graceful upgrade.** With dense embeddings available (a supplied embedder or
  the ``chroma`` backend) the same :class:`~aetherseed.core.rag.retrieval.HybridRetriever`
  fuses lexical and semantic rankings; an optional cross-encoder reranks the top
  candidates. Every upgrade degrades back to the deterministic lexical path.
* **Swappable.** Implementations satisfy the ``VectorIndex`` Protocol in
  :mod:`aetherseed.core.interfaces`; select one with
  :func:`~aetherseed.core.rag.index.get_vector_index`.
"""

from __future__ import annotations

from aetherseed.core.rag.index import (
    ChromaVectorIndex,
    InMemoryVectorIndex,
    NullVectorIndex,
    get_vector_index,
)
from aetherseed.core.rag.retrieval import Document, HybridRetriever, RetrievedDoc

__all__ = [
    "ChromaVectorIndex",
    "Document",
    "HybridRetriever",
    "InMemoryVectorIndex",
    "NullVectorIndex",
    "RetrievedDoc",
    "get_vector_index",
]
