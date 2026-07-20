# ADR 0004 — Ollama over HTTP; cloud opt-in

- Status: Accepted
- Date: 2026-07-20

## Context

We want local LLMs (llama3.1, qwen2.5, deepseek-r1, …) as the default engine, with
an optional cloud fallback. The official `ollama` Python client is convenient but
another dependency; cloud usage must be strictly opt-in for privacy and cost.

## Decision

Talk to **Ollama via its HTTP API using `httpx`** (already a core dependency), so
no extra client library is required and the AI path stays light. Backend
resolution: `null` → NullBackend; else Ollama if reachable; else Anthropic **only
if** the cloud-fallback flag and API key are both set; else NullBackend
(heuristics). Cloud requests never happen implicitly.

## Consequences

- The default install has full local AI capability with no heavy AI libraries.
- The `ai` extra (instructor, sentence-transformers, chromadb) is reserved for
  local embeddings/RAG, not for basic Ollama chat/extraction.
- Adding another backend = implement `LLMBackend`; the engine is agnostic.
