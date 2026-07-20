"""Swap-in interfaces (Protocols + value objects) for the whole platform.

Any scraper, LLM backend, enricher, graph store, asset store, or vector index
can be replaced as long as it satisfies the relevant Protocol here. This is the
seam that dependency injection uses, and it keeps the pipeline agnostic to which
concrete backend is configured.

All Protocols are ``runtime_checkable`` so tests can assert conformance cheaply.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from aetherseed.schemas import Entity, GraphDelta, Provenance, Relationship

# --- Transport-level value objects -------------------------------------------


@dataclass(slots=True)
class FetchResult:
    """The outcome of fetching a single URL.

    ``ok`` is ``True`` only when content was retrieved. On failure ``error``
    holds a structured description and ``content`` is empty.
    """

    url: str
    final_url: str
    status_code: int
    content: bytes = b""
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    rendered: bool = False  # True if produced by a JS-rendering engine
    content_hash: str | None = None

    @property
    def text(self) -> str:
        """Decode content as UTF-8, replacing undecodable bytes."""
        return self.content.decode("utf-8", errors="replace")


@dataclass(slots=True)
class ExtractedContent:
    """Structured result of parsing a fetched page."""

    url: str
    title: str | None = None
    text: str = ""
    links: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnrichmentResult:
    """New facts contributed by an enricher for a given entity."""

    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None


# --- Protocols ---------------------------------------------------------------


@runtime_checkable
class Fetcher(Protocol):
    """Retrieves raw content for a URL (static HTTP or JS-rendered)."""

    name: str

    async def fetch(self, url: str, *, render: bool = False) -> FetchResult:
        """Fetch ``url``; ``render`` requests JS execution where supported."""
        ...

    async def aclose(self) -> None:
        """Release any held resources (connection pools, browsers)."""
        ...


@runtime_checkable
class ContentExtractor(Protocol):
    """Parses fetched content into links, text, and (optionally) entities."""

    name: str

    def extract(self, result: FetchResult) -> ExtractedContent:
        """Parse a :class:`FetchResult` into :class:`ExtractedContent`."""
        ...


@runtime_checkable
class LLMBackend(Protocol):
    """A pluggable large-language-model backend (local or cloud).

    Implementations must degrade gracefully: :meth:`available` reports whether
    the backend can actually serve requests so callers can fall back to
    deterministic heuristics when no model is present.
    """

    name: str
    model: str

    def available(self) -> bool:
        """Whether the backend is reachable and ready to serve requests."""
        ...

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return a free-text completion."""
        ...

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        """Return a validated instance of ``schema`` (structured output)."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts`` (empty list if unsupported)."""
        ...


@runtime_checkable
class Enricher(Protocol):
    """Adds external context to an entity (WHOIS, DNS, registries, ...)."""

    name: str

    def supports(self, entity: Entity) -> bool:
        """Whether this enricher can act on the given entity."""
        ...

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        """Return new facts about ``entity``."""
        ...


@runtime_checkable
class GraphStore(Protocol):
    """Knowledge-graph persistence + query surface."""

    def apply_delta(self, delta: GraphDelta) -> None:
        """Merge nodes and edges (idempotent by id / entity resolution)."""
        ...

    def add_entity(self, entity: Entity) -> str:
        """Add or merge an entity; return its canonical id."""
        ...

    def add_relationship(self, rel: Relationship) -> str:
        """Add or merge a relationship; return its id."""
        ...

    def neighbors(self, entity_id: str) -> list[str]:
        """Return ids of entities directly connected to ``entity_id``."""
        ...

    def shortest_path(self, source_id: str, target_id: str) -> list[str]:
        """Return the shortest path of entity ids, or an empty list."""
        ...

    def export(self, fmt: str) -> str | dict[str, Any]:
        """Export the graph (``graphml`` | ``json-ld`` | ``cytoscape``)."""
        ...


@runtime_checkable
class AssetStore(Protocol):
    """Content-addressable storage for screenshots and downloads."""

    def put(self, data: bytes, *, kind: str, content_type: str | None = None) -> Any:
        """Store bytes, returning an ``AssetRecord``-like object."""
        ...

    def get(self, sha256: str) -> bytes:
        """Retrieve stored bytes by content hash."""
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """Vector store for RAG over the collected corpus."""

    def add(self, ids: Sequence[str], texts: Sequence[str], metadata: Sequence[dict[str, Any]]) -> None:
        """Index documents."""
        ...

    def query(self, text: str, *, k: int = 5) -> list[dict[str, Any]]:
        """Return the ``k`` nearest documents with scores + metadata."""
        ...


@runtime_checkable
class SeedGenerator(Protocol):
    """Produces new seeds from entities, relationships, and gaps."""

    name: str

    def propose(
        self, *, entities: Iterable[Entity], context: str
    ) -> list[dict[str, Any]]:
        """Propose candidate seeds (each a serialisable dict)."""
        ...
