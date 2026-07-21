"""The hybrid retrieval engine: Okapi BM25 + dense cosine, fused with RRF.

This is the deterministic core of the RAG layer. It is intentionally dependency
free — lexical ranking is a from-scratch BM25 implementation and fusion uses
Reciprocal Rank Fusion (RRF), so a useful corpus search works with no model and
no network. Semantic ranking is *layered on top* only when dense vectors are
supplied, and an optional cross-encoder can rerank the fused shortlist.

Why RRF? It combines rankings from heterogeneous scorers (a BM25 score and a
cosine similarity are not on the same scale) without tuning weights, and it is
robust to outliers — the standard choice for cheap, reliable hybrid search.

Examples
--------
>>> r = HybridRetriever()
>>> r.add([Document(id="a", text="acme mining owns beta holdings")])
>>> r.add([Document(id="b", text="unrelated cooking recipe")])
>>> hits = r.query("who owns beta", k=1)
>>> hits[0].id
'a'
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_RRF_K = 60  # RRF damping constant; 60 is the widely used default.


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer used by the lexical index.

    Tokens shorter than two characters are dropped — they carry little signal
    and inflate the vocabulary.
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


@dataclass(slots=True)
class Document:
    """A unit of retrievable text plus optional metadata and a dense vector."""

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass(slots=True)
class RetrievedDoc:
    """A search hit: the document, its fused score, and per-signal detail."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, float] = field(default_factory=dict)

    def snippet(self, max_chars: int = 320) -> str:
        """Return a trimmed single-line preview of the document text."""
        flat = " ".join(self.text.split())
        return flat if len(flat) <= max_chars else flat[: max_chars - 1].rstrip() + "…"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = _RRF_K
) -> dict[str, float]:
    """Fuse several ranked id-lists into a single score map via RRF.

    Each list contributes ``1 / (k + rank)`` per document (rank is 0-based). The
    result is order-independent across lists and needs no score normalisation.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class _BM25:
    """Minimal Okapi BM25 lexical scorer with lazy corpus statistics."""

    __slots__ = ("_avgdl", "_b", "_df", "_dirty", "_docs", "_k1", "_tokens")

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._docs: list[str] = []  # doc ids, positional
        self._tokens: list[Counter[str]] = []  # term frequencies per doc
        self._df: Counter[str] = Counter()  # document frequency per term
        self._avgdl = 0.0
        self._dirty = False

    def add(self, doc_id: str, text: str) -> None:
        tf = Counter(tokenize(text))
        self._docs.append(doc_id)
        self._tokens.append(tf)
        for term in tf:
            self._df[term] += 1
        self._dirty = True

    def _refresh(self) -> None:
        total = sum(sum(tf.values()) for tf in self._tokens)
        self._avgdl = total / len(self._tokens) if self._tokens else 0.0
        self._dirty = False

    def rank(self, query: str, *, limit: int) -> list[str]:
        """Return document ids ordered by descending BM25 relevance."""
        if not self._tokens:
            return []
        if self._dirty:
            self._refresh()
        q_terms = set(tokenize(query))
        if not q_terms:
            return []
        n = len(self._tokens)
        scored: list[tuple[float, str]] = []
        for idx, tf in enumerate(self._tokens):
            dl = sum(tf.values()) or 1
            score = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                df = self._df.get(term, 0) or 1
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = f + self._k1 * (1 - self._b + self._b * dl / (self._avgdl or 1))
                score += idf * (f * (self._k1 + 1)) / denom
            if score > 0.0:
                scored.append((score, self._docs[idx]))
        scored.sort(key=lambda s: (-s[0], s[1]))  # stable, deterministic
        return [doc_id for _, doc_id in scored[:limit]]


class HybridRetriever:
    """Fuses lexical (BM25) and dense (cosine) rankings over a document set.

    Parameters
    ----------
    cross_encoder_model:
        Optional ``sentence-transformers`` cross-encoder name. When set *and* the
        library is installed, the fused shortlist is reranked by the model;
        otherwise the RRF order is used unchanged (the default, deterministic
        path). Kept ``None`` in the offline pipeline.

    Notes
    -----
    The retriever is append-only and holds documents in memory. It is designed
    for a single investigation's corpus (hundreds to low-thousands of pages), not as
    a durable store — use :class:`~aetherseed.core.rag.index.ChromaVectorIndex`
    for persistence.
    """

    def __init__(self, *, cross_encoder_model: str | None = None) -> None:
        self._by_id: dict[str, Document] = {}
        self._bm25 = _BM25()
        self._cross_encoder_model = cross_encoder_model

    def __len__(self) -> int:
        return len(self._by_id)

    def add(self, docs: Sequence[Document]) -> None:
        """Index one or more documents (ids are de-duplicated, last write wins)."""
        for doc in docs:
            if doc.id in self._by_id:
                continue
            self._by_id[doc.id] = doc
            self._bm25.add(doc.id, doc.text)

    def query(
        self,
        text: str,
        *,
        k: int = 5,
        query_embedding: Sequence[float] | None = None,
        candidate_pool: int = 50,
    ) -> list[RetrievedDoc]:
        """Return the top-``k`` documents for ``text``.

        Combines a BM25 ranking with a dense cosine ranking (when embeddings are
        present) via RRF, then optionally reranks with a cross-encoder.
        """
        if not self._by_id:
            return []

        lexical = self._bm25.rank(text, limit=candidate_pool)
        dense = self._dense_rank(query_embedding, limit=candidate_pool)

        rankings = [r for r in (lexical, dense) if r]
        if not rankings:
            return []
        fused = reciprocal_rank_fusion(rankings)

        ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        shortlist = [doc_id for doc_id, _ in ordered[: max(k, candidate_pool)]]
        shortlist = self._maybe_rerank(text, shortlist, k)

        lex_pos = {doc_id: i for i, doc_id in enumerate(lexical)}
        dense_pos = {doc_id: i for i, doc_id in enumerate(dense)}
        out: list[RetrievedDoc] = []
        for doc_id in shortlist[:k]:
            doc = self._by_id[doc_id]
            out.append(
                RetrievedDoc(
                    id=doc_id,
                    text=doc.text,
                    score=round(fused.get(doc_id, 0.0), 6),
                    metadata=dict(doc.metadata),
                    signals={
                        "lexical_rank": float(lex_pos.get(doc_id, -1)),
                        "dense_rank": float(dense_pos.get(doc_id, -1)),
                    },
                )
            )
        return out

    def _dense_rank(
        self, query_embedding: Sequence[float] | None, *, limit: int
    ) -> list[str]:
        if not query_embedding:
            return []
        scored: list[tuple[float, str]] = []
        for doc_id, doc in self._by_id.items():
            if doc.embedding is None:
                continue
            sim = cosine(query_embedding, doc.embedding)
            if sim > 0.0:
                scored.append((sim, doc_id))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [doc_id for _, doc_id in scored[:limit]]

    def _maybe_rerank(self, query: str, doc_ids: list[str], k: int) -> list[str]:
        """Cross-encoder rerank of the shortlist; no-op without the model/library."""
        if not self._cross_encoder_model or len(doc_ids) <= 1:
            return doc_ids
        try:  # pragma: no cover - exercised only when the optional model is present
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(self._cross_encoder_model)
            pairs = [(query, self._by_id[d].text) for d in doc_ids]
            scores = model.predict(pairs)
            reranked = [d for _, d in sorted(zip(scores, doc_ids, strict=True), reverse=True)]
            return reranked[:k] + [d for d in doc_ids if d not in set(reranked[:k])]
        except Exception:  # pragma: no cover - degrade to fused order on any failure
            return doc_ids
