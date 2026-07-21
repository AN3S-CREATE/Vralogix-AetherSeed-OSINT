"""Durable graph backend: Neo4j behind the ``GraphStore`` Protocol.

This is the canonical "next backend" for scale and persistence. The in-run
analysis path stays on :class:`~aetherseed.core.graph.store.NetworkXGraphStore`
(fast, in-memory, feeds follow-the-money); ``Neo4jGraphStore`` gives durable,
queryable storage of the resolved graph across runs.

Key properties, consistent with the platform's invariants:

* **Optional.** ``neo4j`` is imported lazily inside methods, so importing this
  module never requires the driver or a running database. Absent/unreachable
  Neo4j degrades (via :func:`~aetherseed.core.graph.factory.get_graph_store`) to
  the in-memory store.
* **Injection-friendly.** A pre-built ``driver`` can be supplied (used by tests
  to run fully offline against a fake driver).
* **Safe queries.** All Cypher is constant text with bound parameters — no string
  interpolation of entity data, so there is no injection surface. A single fixed
  relationship label (``:REL``) with a ``type`` property avoids dynamic labels.
* **Deterministic identity.** Nodes are keyed by the same canonical key used by
  entity resolution, so repeated ingests converge instead of duplicating.
"""

from __future__ import annotations

import json
from typing import Any

from aetherseed.config import Settings, get_settings
from aetherseed.core.graph.resolution import canonical_key
from aetherseed.errors import BackendUnavailableError, ConfigurationError
from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, GraphDelta, Relationship

log = get_logger(__name__)


class Neo4jGraphStore:
    """A ``GraphStore`` backed by Neo4j (Bolt).

    Parameters
    ----------
    settings:
        Application settings (uri/user/password read from these).
    driver:
        Optional pre-constructed Neo4j driver. When omitted, a driver is built
        lazily from settings on first use. Supplying one keeps the store fully
        testable without a live database.
    """

    name = "neo4j"

    def __init__(self, settings: Settings | None = None, *, driver: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._driver = driver

    # --- Connection ----------------------------------------------------------

    def _get_driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        try:
            import neo4j  # local import keeps ``neo4j`` optional
        except ImportError as exc:  # pragma: no cover - exercised via factory degrade path
            raise BackendUnavailableError(
                "neo4j driver not installed (`uv sync --extra graph`)"
            ) from exc
        if not self._settings.neo4j_password:
            raise ConfigurationError("AETHERSEED_NEO4J_PASSWORD is required for the neo4j backend")
        self._driver = neo4j.GraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password),
        )
        return self._driver

    def available(self) -> bool:
        """Whether the backend is reachable and ready (best-effort ping)."""
        try:
            self._run("RETURN 1 AS ok")
            return True
        except (BackendUnavailableError, ConfigurationError):
            return False
        except Exception as exc:  # driver/connection errors
            log.warning("neo4j.unavailable", error=str(exc))
            return False

    def close(self) -> None:
        """Close the underlying driver if we created one."""
        if self._driver is not None and hasattr(self._driver, "close"):
            self._driver.close()

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        driver = self._get_driver()
        with driver.session() as session:
            result = session.run(query, params)
            return [dict(record) for record in result]

    # --- Mutation ------------------------------------------------------------

    def add_entity(self, entity: Entity) -> str:
        """MERGE an entity by canonical key; return that key (its stable id)."""
        key = canonical_key(entity)
        self._run(
            """
            MERGE (n:Entity {key: $key})
            SET n.type = $type, n.label = $label, n.aliases = $aliases,
                n.confidence = $confidence, n.attributes = $attributes,
                n.entity_id = coalesce(n.entity_id, $entity_id)
            """,
            key=key,
            type=entity.type.value,
            label=entity.label,
            aliases=list(entity.aliases),
            confidence=entity.confidence,
            attributes=json.dumps(entity.attributes, default=str),
            entity_id=entity.id,
        )
        return key

    def add_relationship(self, rel: Relationship) -> str:
        """MERGE a relationship edge between two already-resolved node keys."""
        self._run(
            """
            MATCH (a:Entity {key: $source}), (b:Entity {key: $target})
            MERGE (a)-[r:REL {id: $id}]->(b)
            SET r.type = $type, r.label = $label, r.weight = $weight,
                r.confidence = $confidence, r.attributes = $attributes
            """,
            source=rel.source_id,
            target=rel.target_id,
            id=rel.id,
            type=rel.type.value,
            label=rel.label,
            weight=rel.weight,
            confidence=rel.confidence,
            attributes=json.dumps(rel.attributes, default=str),
        )
        return rel.id

    def apply_delta(self, delta: GraphDelta) -> None:
        """Merge nodes then edges, remapping edge endpoints through resolution."""
        remap: dict[str, str] = {node.id: self.add_entity(node) for node in delta.nodes}
        for edge in delta.edges:
            src = remap.get(edge.source_id, edge.source_id)
            dst = remap.get(edge.target_id, edge.target_id)
            self.add_relationship(edge.model_copy(update={"source_id": src, "target_id": dst}))

    # --- Queries -------------------------------------------------------------

    def neighbors(self, entity_id: str) -> list[str]:
        """Return keys of entities directly connected to ``entity_id``."""
        rows = self._run(
            "MATCH (n:Entity {key: $key})-[:REL]-(m:Entity) RETURN DISTINCT m.key AS key",
            key=entity_id,
        )
        return [r["key"] for r in rows if r.get("key")]

    def shortest_path(self, source_id: str, target_id: str) -> list[str]:
        """Return the shortest undirected path of node keys, or an empty list."""
        rows = self._run(
            """
            MATCH p = shortestPath(
                (a:Entity {key: $source})-[:REL*..15]-(b:Entity {key: $target})
            )
            RETURN [n IN nodes(p) | n.key] AS path
            """,
            source=source_id,
            target=target_id,
        )
        if not rows:
            return []
        return list(rows[0].get("path") or [])

    def export(self, fmt: str = "node-link") -> str | dict[str, Any]:
        """Export the persisted graph. Supports ``node-link`` and ``json-ld``."""
        fmt = fmt.lower()
        if fmt not in ("node-link", "json", "json-ld"):
            raise ValueError(f"neo4j export supports node-link | json-ld, not {fmt!r}")
        nodes = self._run(
            "MATCH (n:Entity) RETURN n.key AS id, n.label AS label, n.type AS type, "
            "n.aliases AS aliases"
        )
        edges = self._run(
            "MATCH (a:Entity)-[r:REL]->(b:Entity) "
            "RETURN r.id AS id, a.key AS source, b.key AS target, r.type AS type, "
            "r.confidence AS confidence"
        )
        if fmt == "json-ld":
            return {
                "@context": {"label": "http://schema.org/name"},
                "@graph": {"nodes": nodes, "edges": edges},
            }
        return {
            "directed": True,
            "multigraph": True,
            "nodes": [{"id": n["id"], **{k: n[k] for k in ("label", "type", "aliases")}} for n in nodes],
            "links": edges,
        }
