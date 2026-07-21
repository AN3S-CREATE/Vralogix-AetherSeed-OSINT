"""Neo4j graph-store tests — fully offline against a fake Bolt driver.

The store is exercised without a live database by injecting a fake driver that
records Cypher and returns canned records. This verifies query construction,
parameter binding (no string interpolation), delta remapping, result mapping,
and the factory's graceful degradation.
"""

from __future__ import annotations

from typing import Any

import pytest
from aetherseed.config import Settings, get_settings
from aetherseed.core.graph.factory import get_graph_store
from aetherseed.core.graph.neo4j_store import Neo4jGraphStore
from aetherseed.schemas import Entity, EntityType, GraphDelta, Relationship, RelationType


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    def __init__(self, driver: FakeNeo4jDriver) -> None:
        self._driver = driver

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def run(self, query: str, params: dict[str, Any] | None = None) -> _FakeResult:
        self._driver.queries.append((query, dict(params or {})))
        for pattern, rows in self._driver.responses.items():
            if pattern in query:
                return _FakeResult(rows)
        return _FakeResult([])


class FakeNeo4jDriver:
    """Records every query and returns canned rows for substring-matched patterns."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses or {}
        self.closed = False

    def session(self) -> _FakeSession:
        return _FakeSession(self)

    def close(self) -> None:
        self.closed = True


# --- Mutation ----------------------------------------------------------------


def test_apply_delta_issues_parameterised_merges() -> None:
    driver = FakeNeo4jDriver()
    store = Neo4jGraphStore(driver=driver)
    a = Entity(type=EntityType.COMPANY, label="Acme Ltd")
    b = Entity(type=EntityType.COMPANY, label="Beta Holdings")
    rel = Relationship(source_id=a.id, target_id=b.id, type=RelationType.OWNS)

    store.apply_delta(GraphDelta(nodes=[a, b], edges=[rel]))

    joined = " ".join(q for q, _ in driver.queries)
    assert "MERGE (n:Entity {key: $key})" in joined
    assert "MERGE (a)-[r:REL {id: $id}]->(b)" in joined
    # Edge endpoints were remapped to canonical keys and bound as parameters.
    edge_calls = [p for q, p in driver.queries if "REL" in q]
    assert edge_calls
    assert edge_calls[0]["source"].startswith("company:")
    assert edge_calls[0]["target"].startswith("company:")


def test_add_entity_returns_canonical_key() -> None:
    store = Neo4jGraphStore(driver=FakeNeo4jDriver())
    key = store.add_entity(Entity(type=EntityType.COMPANY, label="Acme (Pty) Ltd"))
    assert key == "company:acme"  # legal suffixes stripped by canonical_key


# --- Queries -----------------------------------------------------------------


def test_neighbors_and_shortest_path_map_records() -> None:
    driver = FakeNeo4jDriver(
        responses={
            "RETURN DISTINCT m.key": [{"key": "company:beta"}, {"key": "person:jane"}],
            "RETURN [n IN nodes(p)": [{"path": ["a", "b", "c"]}],
        }
    )
    store = Neo4jGraphStore(driver=driver)
    assert store.neighbors("company:acme") == ["company:beta", "person:jane"]
    assert store.shortest_path("a", "c") == ["a", "b", "c"]


def test_shortest_path_empty_when_no_path() -> None:
    store = Neo4jGraphStore(driver=FakeNeo4jDriver())
    assert store.shortest_path("a", "z") == []


# --- Export ------------------------------------------------------------------


def test_export_node_link_and_jsonld() -> None:
    driver = FakeNeo4jDriver(
        responses={
            "MATCH (n:Entity) RETURN n.key": [
                {"id": "company:acme", "label": "Acme", "type": "company", "aliases": []}
            ],
            "MATCH (a:Entity)-[r:REL]->(b:Entity)": [
                {"id": "r1", "source": "company:acme", "target": "company:beta",
                 "type": "owns", "confidence": 0.5}
            ],
        }
    )
    store = Neo4jGraphStore(driver=driver)

    node_link = store.export("node-link")
    assert isinstance(node_link, dict)
    assert node_link["nodes"][0]["id"] == "company:acme"
    assert node_link["links"][0]["id"] == "r1"

    json_ld = store.export("json-ld")
    assert isinstance(json_ld, dict) and "@graph" in json_ld


def test_export_rejects_unsupported_format() -> None:
    store = Neo4jGraphStore(driver=FakeNeo4jDriver())
    with pytest.raises(ValueError, match="node-link"):
        store.export("graphml")


# --- Availability / lifecycle ------------------------------------------------


def test_available_true_when_ping_succeeds() -> None:
    store = Neo4jGraphStore(driver=FakeNeo4jDriver(responses={"RETURN 1": [{"ok": 1}]}))
    assert store.available() is True


def test_available_false_when_driver_errors() -> None:
    class _BadDriver:
        def session(self) -> Any:
            raise RuntimeError("connection refused")

    assert Neo4jGraphStore(driver=_BadDriver()).available() is False


def test_available_false_without_driver_or_credentials(env: Settings) -> None:
    # No injected driver: lazy import / config checks run and fail safe (never raises).
    assert Neo4jGraphStore(env).available() is False


def test_close_delegates_to_driver() -> None:
    driver = FakeNeo4jDriver()
    Neo4jGraphStore(driver=driver).close()
    assert driver.closed is True


# --- Factory -----------------------------------------------------------------


def test_factory_defaults_to_networkx(env: Settings) -> None:
    assert get_graph_store(env).__class__.__name__ == "NetworkXGraphStore"


def test_factory_degrades_to_networkx_when_neo4j_unavailable(
    env: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AETHERSEED_GRAPH_BACKEND", "neo4j")
    monkeypatch.setenv("AETHERSEED_NEO4J_PASSWORD", "")  # missing creds / unreachable
    get_settings.cache_clear()
    store = get_graph_store(get_settings())
    assert store.__class__.__name__ == "NetworkXGraphStore"
