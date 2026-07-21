"""Follow-the-money analysis tests."""

from __future__ import annotations

import pytest
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


def test_timeline_orders_dated_entities_oldest_first() -> None:
    store = NetworkXGraphStore()
    store.add_entity(
        Entity(type=EntityType.TRANSACTION, label="Acme wires funds offshore",
               attributes={"date": "2020-01-15", "amount": 1000})
    )
    store.add_entity(
        Entity(type=EntityType.TRANSACTION, label="Beta receives a dividend",
               attributes={"date": "2019-06-01", "amount": 500})
    )
    store.add_entity(Entity(type=EntityType.COMPANY, label="Undated Co"))  # omitted

    timeline = FollowTheMoney(store).timeline()
    assert [e["label"] for e in timeline] == [
        "Beta receives a dividend",
        "Acme wires funds offshore",
    ]
    assert timeline[0]["amount"] == 500


def test_geo_points_extracted_from_coordinates() -> None:
    store = NetworkXGraphStore()
    store.add_entity(
        Entity(type=EntityType.LOCATION, label="Head Office",
               attributes={"lat": "-26.2041", "lon": "28.0473"})
    )
    store.add_entity(Entity(type=EntityType.LOCATION, label="No Coordinates"))

    points = FollowTheMoney(store).geo_points()
    assert len(points) == 1
    assert points[0]["label"] == "Head Office"
    assert points[0]["lat"] == pytest.approx(-26.2041)


def test_analyze_report_includes_timeline_and_geo() -> None:
    store = NetworkXGraphStore()
    store.add_entity(
        Entity(type=EntityType.TRANSACTION, label="Single payment",
               attributes={"date": "2021-03-03"})
    )
    report = FollowTheMoney(store).analyze().to_dict()
    assert report["timeline"] and report["timeline"][0]["label"] == "Single payment"
    assert report["geo"] == []
