"""Hybrid RAG retrieval, vector-index backends, and lead grounding tests.

All offline and deterministic — the default in-memory index is pure Python, so
no model, service, or optional dependency is exercised here.
"""

from __future__ import annotations

import pytest
from aetherseed.config import Settings, get_settings
from aetherseed.core.rag.index import (
    InMemoryVectorIndex,
    NullVectorIndex,
    get_vector_index,
)
from aetherseed.core.rag.retrieval import (
    Document,
    HybridRetriever,
    cosine,
    reciprocal_rank_fusion,
    tokenize,
)
from aetherseed.pipelines import InvestigationPipeline
from aetherseed.schemas import Constraints, SubjectSeed, SubjectType

from tests.fakes import FakeFetcher

# --- Primitives --------------------------------------------------------------


def test_tokenize_lowercases_and_drops_singletons() -> None:
    assert tokenize("The ACME-2 Mining Co!") == ["the", "acme", "mining", "co"]


def test_cosine_handles_degenerate_input() -> None:
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)


def test_rrf_is_symmetric_for_swapped_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    assert fused["a"] == pytest.approx(fused["b"])


# --- HybridRetriever ---------------------------------------------------------


def test_bm25_ranks_most_relevant_document_first() -> None:
    r = HybridRetriever()
    r.add(
        [
            Document(id="a", text="acme mining owns beta holdings and controls gamma"),
            Document(id="b", text="a totally unrelated recipe for chocolate cake"),
            Document(id="c", text="beta holdings annual report and directors"),
        ]
    )
    hits = r.query("who owns beta holdings", k=2)
    assert hits[0].id == "a"  # most query-term overlap
    assert "b" not in {h.id for h in hits}  # zero overlap -> excluded


def test_empty_corpus_and_empty_query_return_nothing() -> None:
    r = HybridRetriever()
    assert r.query("anything") == []
    r.add([Document(id="a", text="hello world")])
    assert r.query("") == []  # no query terms


def test_dense_ranking_kicks_in_when_lexical_misses() -> None:
    r = HybridRetriever()
    r.add(
        [
            Document(id="a", text="alpha", embedding=[1.0, 0.0]),
            Document(id="b", text="beta", embedding=[0.0, 1.0]),
        ]
    )
    # Query token overlaps neither doc; the dense vector decides.
    hits = r.query("zzz", k=1, query_embedding=[0.9, 0.1])
    assert hits and hits[0].id == "a"


def test_duplicate_ids_are_ignored() -> None:
    r = HybridRetriever()
    r.add([Document(id="a", text="first")])
    r.add([Document(id="a", text="second overwrite attempt")])
    assert len(r) == 1


def test_retrieved_snippet_is_trimmed() -> None:
    r = HybridRetriever()
    r.add([Document(id="a", text="word " * 200)])
    hit = r.query("word", k=1)[0]
    assert len(hit.snippet(max_chars=50)) <= 50


# --- Vector index backends ---------------------------------------------------


def test_inmemory_index_add_query_roundtrip() -> None:
    idx = InMemoryVectorIndex()
    idx.add(
        ["u1", "u2"],
        ["acme owns beta holdings", "an unrelated cooking blog"],
        [{"url": "http://a"}, {"url": "http://b"}],
    )
    assert len(idx) == 2
    hits = idx.query("who owns beta", k=1)
    assert hits and hits[0]["id"] == "u1"
    assert hits[0]["metadata"]["url"] == "http://a"
    assert hits[0]["snippet"] and hits[0]["score"] >= 0.0


def test_inmemory_index_rejects_mismatched_lengths() -> None:
    idx = InMemoryVectorIndex()
    with pytest.raises(ValueError, match="same length"):
        idx.add(["u1"], ["a", "b"], [{}])


def test_inmemory_index_skips_blank_text() -> None:
    idx = InMemoryVectorIndex()
    idx.add(["u1"], ["   "], [{}])
    assert len(idx) == 0


def test_null_index_is_inert() -> None:
    idx = NullVectorIndex()
    idx.add(["u1"], ["text"], [{}])
    assert len(idx) == 0
    assert idx.query("text") == []


# --- Factory (local-first degradation) --------------------------------------


def test_factory_defaults_to_memory(env: Settings) -> None:
    assert get_vector_index(env).name == "memory"


def test_factory_none_backend_disables_rag(env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERSEED_VECTOR_BACKEND", "none")
    get_settings.cache_clear()
    assert get_vector_index(get_settings()).name == "none"


def test_factory_rag_disabled_flag_disables_rag(
    env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AETHERSEED_RAG_ENABLED", "false")
    get_settings.cache_clear()
    assert get_vector_index(get_settings()).name == "none"


def test_factory_chroma_degrades_gracefully(
    env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AETHERSEED_VECTOR_BACKEND", "chroma")
    get_settings.cache_clear()
    # "chroma" if the optional lib is installed, else transparent fallback to memory.
    assert get_vector_index(get_settings()).name in {"chroma", "memory"}


def test_chroma_index_roundtrip_when_available() -> None:
    """Exercises the ChromaDB backend only when the ``ai`` extra is installed."""
    pytest.importorskip("chromadb")
    from aetherseed.core.rag.index import ChromaVectorIndex

    idx = ChromaVectorIndex()
    idx.add(
        ["u1", "u2"],
        ["acme owns beta holdings", "an unrelated cooking blog"],
        [{"url": "http://a"}, {"url": "http://b"}],
    )
    assert len(idx) == 2
    assert idx.query("", k=1) == []  # blank query short-circuits
    hits = idx.query("who owns beta", k=2)
    assert hits and hits[0]["id"] in {"u1", "u2"}
    assert hits[0]["snippet"] and "score" in hits[0]


# --- Pipeline integration: leads are grounded with evidence ------------------

_PAGES = {
    "http://example.com/": "<h1>Root Co Pty Ltd</h1><a href='/about'>about</a>",
    "http://example.com/about": (
        "<p>Root Co Pty Ltd is owned by Parent Holdings Ltd, its majority "
        "shareholder. Parent Holdings Ltd controls several subsidiaries.</p>"
    ),
}


async def test_pipeline_attaches_evidence_to_leads(env: Settings) -> None:
    pipe = InvestigationPipeline(env, fetcher_factory=lambda respect_robots: FakeFetcher(_PAGES))
    subject = SubjectSeed(
        subject_type=SubjectType.COMPANY,
        primary_identifiers=["http://example.com/"],
        context="ownership",
        constraints=Constraints(max_depth=1, max_pages=10, require_approval=False),
    )
    result = await pipe.run(subject)

    grounded = [lead for lead in result.new_leads if lead.evidence]
    assert grounded, "expected at least one lead grounded with corpus evidence"
    snippet = grounded[0].evidence[0]
    assert snippet.text and snippet.score >= 0.0


async def test_pipeline_rag_can_be_disabled(env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHERSEED_RAG_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    pipe = InvestigationPipeline(settings, fetcher_factory=lambda respect_robots: FakeFetcher(_PAGES))
    subject = SubjectSeed(
        subject_type=SubjectType.COMPANY,
        primary_identifiers=["http://example.com/"],
        constraints=Constraints(max_depth=1, max_pages=10, require_approval=False),
    )
    result = await pipe.run(subject)
    assert all(not lead.evidence for lead in result.new_leads)
