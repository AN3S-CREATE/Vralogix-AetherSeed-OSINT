# ADR 0006 — Hybrid RAG grounding of leads (local-first)

- Status: Accepted
- Date: 2026-07-21

## Context

Leads and gap analysis were structurally sound but *ungrounded*: an investigator
could not see the exact page text that justified a finding without re-reading the
corpus. We wanted retrieval-augmented grounding — attach the supporting passage
to each lead — without breaking the offline-first invariant or adding a mandatory
vector database / embedding model.

## Decision

Add a RAG layer (`aetherseed/core/rag/`) built around a dependency-free
`HybridRetriever`:

- **Lexical**: a from-scratch Okapi BM25 scorer (no external library).
- **Dense**: cosine over supplied embeddings — *optional*, layered on only when
  vectors are present.
- **Fusion**: Reciprocal Rank Fusion (RRF) combines the rankings without score
  normalisation or weight tuning; an optional cross-encoder can rerank the
  shortlist and degrades to RRF order when absent.

Three `VectorIndex` backends satisfy the existing Protocol and are chosen by
`get_vector_index`: `InMemoryVectorIndex` (default, offline), `ChromaVectorIndex`
(dense, `ai` extra), and `NullVectorIndex` (disabled). The pipeline indexes each
fetched page's text into a per-run corpus and attaches the top snippets to every
lead as `EvidenceSnippet`s (source URL + score), fully audited.

## Consequences

- Corpus search and lead grounding work with an empty `.env` and no model —
  deterministic and testable. `chroma` is a graceful upgrade, not a requirement.
- The default `vector_backend` changes from `chroma` to `memory` so the feature
  is on by default without the heavy `ai` extra.
- Retrieval is best-effort: any failure logs and leaves the lead unchanged,
  preserving the run (consistent with per-item fault isolation).
- Follows the same swap-seam pattern as fetchers/backends, so a future
  reranking model or vector store drops in behind `VectorIndex`.
