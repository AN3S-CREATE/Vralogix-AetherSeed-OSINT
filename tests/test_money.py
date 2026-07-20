"""Follow-the-money analysis tests."""

from __future__ import annotations

from aetherseed.core.graph.money import FollowTheMoney
from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.schemas import Entity, EntityType, Relationship, RelationType


def _mk(store: NetworkXGraphStore, label: str, etype: EntityType = EntityType.COMPANY) -> str:
    return store.add_entity(Entity(type=etype, label=label))


def test_ownership_chain_detected() -> None:
    store = NetworkXGraphStore()
    a, b, c = _mk(store, "A"), _mk(store, "B"), _mk(store, "C")
    store.add_relationship(Relationship(source_id=a, target_id=b, type=RelationType.OWNS))
    store.add_relationship(Relationship(source_id=b, target_id=c, type=RelationType.OWNS))
    report = FollowTheMoney(store).analyze()
    assert any(chain.path == [a, b, c] for chain in report.ownership_chains)


def test_circular_ownership_is_red_flag() -> None:
    store = NetworkXGraphStore()
    a, b = _mk(store, "A"), _mk(store, "B")
    store.add_relationship(Relationship(source_id=a, target_id=b, type=RelationType.OWNS))
    store.add_relationship(Relationship(source_id=b, target_id=a, type=RelationType.OWNS))
    flags = {f.signal for f in FollowTheMoney(store).red_flags()}
    assert "circular_ownership" in flags


def test_shared_director_detected() -> None:
    store = NetworkXGraphStore()
    jane = _mk(store, "Jane Doe", EntityType.PERSON)
    co1, co2 = _mk(store, "Co One"), _mk(store, "Co Two")
    store.add_relationship(Relationship(source_id=jane, target_id=co1, type=RelationType.DIRECTOR_OF))
    store.add_relationship(Relationship(source_id=jane, target_id=co2, type=RelationType.DIRECTOR_OF))
    shared = FollowTheMoney(store).shared_directors()
    assert shared and shared[0]["director"] == "Jane Doe"
    assert len(shared[0]["companies"]) == 2
