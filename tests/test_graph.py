"""Knowledge-graph store tests."""

from __future__ import annotations

import pytest
from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.schemas import Entity, EntityType, GraphDelta, Relationship, RelationType


def _co(label: str) -> Entity:
    return Entity(type=EntityType.COMPANY, label=label)


def test_apply_delta_and_paths() -> None:
    store = NetworkXGraphStore()
    a, b, c = _co("A Ltd"), _co("B Ltd"), _co("C Ltd")
    delta = GraphDelta(
        nodes=[a, b, c],
        edges=[
            Relationship(source_id=a.id, target_id=b.id, type=RelationType.OWNS),
            Relationship(source_id=b.id, target_id=c.id, type=RelationType.OWNS),
        ],
    )
    store.apply_delta(delta)
    assert store.stats()["nodes"] == 3
    assert store.stats()["edges"] == 2
    assert store.shortest_path(a.id, c.id) == [a.id, b.id, c.id]
    assert set(store.neighbors(b.id)) == {a.id, c.id}


def test_entity_resolution_merges_duplicates() -> None:
    store = NetworkXGraphStore()
    id1 = store.add_entity(_co("Acme Mining (Pty) Ltd"))
    id2 = store.add_entity(_co("ACME Mining"))
    assert id1 == id2
    assert store.stats()["nodes"] == 1


@pytest.mark.parametrize("fmt", ["node-link", "cytoscape", "json-ld", "graphml"])
def test_export_formats(fmt: str) -> None:
    store = NetworkXGraphStore()
    a, b = _co("A"), _co("B")
    store.apply_delta(
        GraphDelta(nodes=[a, b], edges=[Relationship(source_id=a.id, target_id=b.id, type=RelationType.OWNS)])
    )
    out = store.export(fmt)
    assert out  # non-empty string or dict
    if fmt == "graphml":
        assert isinstance(out, str) and "graphml" in out


def test_unknown_export_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        NetworkXGraphStore().export("bogus")
