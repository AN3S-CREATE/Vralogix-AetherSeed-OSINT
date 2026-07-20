"""In-memory knowledge graph (NetworkX) with entity resolution and export.

Implements the :class:`~aetherseed.core.interfaces.GraphStore` protocol on a
``MultiDiGraph``. Every insert runs entity resolution so the graph converges on
canonical nodes. Analysis helpers (centrality, community detection, path
finding) and multiple export formats (GraphML, JSON-LD, Cytoscape, node-link)
are provided. A monotonically increasing ``version`` supports temporal snapshots.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import networkx as nx

from aetherseed.core.graph.resolution import canonical_key, find_match, merge_into
from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, EntityType, GraphDelta, Relationship

log = get_logger(__name__)


class NetworkXGraphStore:
    """A resolving, exportable in-memory knowledge graph."""

    def __init__(self, graph_id: str | None = None) -> None:
        self.graph_id = graph_id or f"graph_{uuid.uuid4().hex[:12]}"
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict[str, Entity] = {}
        self._key_to_id: dict[str, str] = {}
        self.version = 0

    # --- Mutation ------------------------------------------------------------

    def add_entity(self, entity: Entity) -> str:
        """Add or merge ``entity``; return the canonical node id."""
        key = canonical_key(entity)
        now = datetime.now(UTC).isoformat()

        if key in self._key_to_id:
            node_id = self._key_to_id[key]
            merge_into(self._entities[node_id], entity)
            self._g.nodes[node_id]["last_seen"] = now
            self._sync_node(node_id)
            return node_id

        same_type = [e for e in self._entities.values() if e.type is entity.type]
        match = find_match(entity, same_type)
        if match is not None:
            merge_into(match, entity)
            self._key_to_id[key] = match.id
            self._g.nodes[match.id]["last_seen"] = now
            self._sync_node(match.id)
            return match.id

        node_id = entity.id
        self._entities[node_id] = entity
        self._key_to_id[key] = node_id
        self._g.add_node(node_id, first_seen=now, last_seen=now)
        self._sync_node(node_id)
        return node_id

    def add_relationship(self, rel: Relationship) -> str:
        """Add a relationship edge; skips if either endpoint is unknown."""
        if rel.source_id not in self._g or rel.target_id not in self._g:
            log.debug("graph.edge_skipped_missing_node", rel=rel.id)
            return rel.id
        self._g.add_edge(
            rel.source_id,
            rel.target_id,
            key=rel.id,
            type=rel.type.value,
            label=rel.label,
            weight=rel.weight,
            confidence=rel.confidence,
            attributes=rel.attributes,
        )
        return rel.id

    def apply_delta(self, delta: GraphDelta) -> None:
        """Merge nodes then edges, remapping ids through entity resolution."""
        remap: dict[str, str] = {}
        for node in delta.nodes:
            remap[node.id] = self.add_entity(node)
        for edge in delta.edges:
            src = remap.get(edge.source_id, edge.source_id)
            dst = remap.get(edge.target_id, edge.target_id)
            self.add_relationship(
                edge.model_copy(update={"source_id": src, "target_id": dst})
            )
        self.version += 1

    def _sync_node(self, node_id: str) -> None:
        ent = self._entities[node_id]
        self._g.nodes[node_id].update(
            {
                "type": ent.type.value,
                "label": ent.label,
                "aliases": list(ent.aliases),
                "confidence": ent.confidence,
                "attributes": dict(ent.attributes),
            }
        )

    # --- Queries -------------------------------------------------------------

    def get_entity(self, node_id: str) -> Entity | None:
        return self._entities.get(node_id)

    def neighbors(self, entity_id: str) -> list[str]:
        if entity_id not in self._g:
            return []
        und = self._g.to_undirected(as_view=True)
        return list(und.neighbors(entity_id))

    def shortest_path(self, source_id: str, target_id: str) -> list[str]:
        """Shortest connection path (undirected) between two entities."""
        if source_id not in self._g or target_id not in self._g:
            return []
        und = self._g.to_undirected(as_view=True)
        try:
            return list(nx.shortest_path(und, source_id, target_id))
        except nx.NetworkXNoPath:
            return []

    def degree_centrality(self) -> dict[str, float]:
        if not self._g.number_of_nodes():
            return {}
        return cast("dict[str, float]", nx.degree_centrality(self._g))

    def betweenness_centrality(self) -> dict[str, float]:
        if self._g.number_of_nodes() < 3:
            return {}
        return cast("dict[str, float]", nx.betweenness_centrality(self._g.to_undirected(as_view=True)))

    def communities(self) -> list[list[str]]:
        """Detect communities (Louvain if available, else greedy modularity)."""
        if self._g.number_of_edges() == 0:
            return [[n] for n in self._g.nodes]
        und = nx.Graph(self._g.to_undirected())
        try:
            import community as community_louvain  # python-louvain

            partition = community_louvain.best_partition(und)
            groups: dict[int, list[str]] = {}
            for node, comm in partition.items():
                groups.setdefault(comm, []).append(node)
            return list(groups.values())
        except ImportError:
            comms = nx.community.greedy_modularity_communities(und)
            return [list(c) for c in comms]

    def key_players(self, top: int = 10) -> list[tuple[str, float]]:
        """Highest-centrality nodes (label, score) — the 'hubs' of the network."""
        cent = self.degree_centrality()
        ranked = sorted(cent.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return [(self._entities[nid].label if nid in self._entities else nid, sc) for nid, sc in ranked]

    def stats(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "version": self.version,
            "nodes": self._g.number_of_nodes(),
            "edges": self._g.number_of_edges(),
            "types": self._type_counts(),
        }

    def _type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ent in self._entities.values():
            counts[ent.type.value] = counts.get(ent.type.value, 0) + 1
        return counts

    # --- Export --------------------------------------------------------------

    def export(self, fmt: str = "node-link") -> str | dict[str, Any]:
        """Export the graph in ``fmt``: node-link | cytoscape | json-ld | graphml."""
        fmt = fmt.lower()
        if fmt in ("node-link", "json"):
            return cast("dict[str, Any]", nx.node_link_data(self._g, edges="links"))
        if fmt == "cytoscape":
            return cast("dict[str, Any]", nx.cytoscape_data(self._g))
        if fmt == "json-ld":
            return self._to_jsonld()
        if fmt == "graphml":
            return "\n".join(nx.generate_graphml(self._sanitized_for_graphml()))
        raise ValueError(f"unsupported export format: {fmt}")

    def _to_jsonld(self) -> dict[str, Any]:
        nodes = [
            {
                "@id": nid,
                "@type": self._entities[nid].type.value if nid in self._entities else "node",
                "label": data.get("label", nid),
                "aliases": data.get("aliases", []),
            }
            for nid, data in self._g.nodes(data=True)
        ]
        edges = [
            {
                "@id": key,
                "source": u,
                "target": v,
                "relation": data.get("type"),
                "confidence": data.get("confidence"),
            }
            for u, v, key, data in self._g.edges(keys=True, data=True)
        ]
        return {
            "@context": {"label": "http://schema.org/name", "relation": "http://schema.org/relatedTo"},
            "@graph": {"id": self.graph_id, "version": self.version, "nodes": nodes, "edges": edges},
        }

    def _sanitized_for_graphml(self) -> nx.MultiDiGraph:
        """GraphML only supports scalar attributes; JSON-encode complex ones."""
        h = nx.MultiDiGraph()
        for nid, data in self._g.nodes(data=True):
            h.add_node(nid, **{k: self._scalar(v) for k, v in data.items()})
        for u, v, key, data in self._g.edges(keys=True, data=True):
            h.add_edge(u, v, key=key, **{k: self._scalar(val) for k, val in data.items()})
        return h

    @staticmethod
    def _scalar(value: Any) -> str | int | float | bool:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value if value is not None else ""
        return json.dumps(value, default=str)

    def snapshot(self) -> dict[str, Any]:
        """Serialisable snapshot for temporal versioning / persistence."""
        return {
            "graph_id": self.graph_id,
            "version": self.version,
            "captured_at": datetime.now(UTC).isoformat(),
            "data": nx.node_link_data(self._g, edges="links"),
        }

    def to_records(self) -> tuple[list[Entity], list[dict[str, Any]]]:
        """Return (entities, edge-dicts) for durable persistence."""
        edges = [
            {
                "id": key,
                "source_id": u,
                "target_id": v,
                "type": data.get("type"),
                "label": data.get("label"),
                "weight": data.get("weight", 1.0),
                "confidence": data.get("confidence", 0.5),
                "attributes": data.get("attributes", {}),
            }
            for u, v, key, data in self._g.edges(keys=True, data=True)
        ]
        return list(self._entities.values()), edges

    @property
    def graph(self) -> nx.MultiDiGraph:
        """Read access to the underlying NetworkX graph (for analysis modules)."""
        return self._g

    @property
    def entity_ids_by_type(self) -> dict[EntityType, list[str]]:
        out: dict[EntityType, list[str]] = {}
        for nid, ent in self._entities.items():
            out.setdefault(ent.type, []).append(nid)
        return out
