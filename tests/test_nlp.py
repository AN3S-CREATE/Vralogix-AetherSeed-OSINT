"""Deterministic NLP extraction tests."""

from __future__ import annotations

from aetherseed.core.nlp import extract_entities
from aetherseed.schemas import EntityType


def test_extracts_mixed_entities() -> None:
    text = (
        "Contact jane@acme.co.za about Acme Mining Pty Ltd. "
        "Payment of R1 500 000 to Beta Logistics CC. Visit beta-logistics.co.za "
        "and follow @acmemining."
    )
    ents = extract_entities(text, source_url="http://x/y")
    by_type: dict[EntityType, list[str]] = {}
    for e in ents:
        by_type.setdefault(e.type, []).append(e.label)

    assert any("acme.co.za" in a for a in by_type.get(EntityType.ACCOUNT, []))
    assert any("Acme Mining" in c for c in by_type.get(EntityType.COMPANY, []))
    assert EntityType.TRANSACTION in by_type  # money amount
    assert any(d == "beta-logistics.co.za" for d in by_type.get(EntityType.DOMAIN, []))
    # every entity carries provenance
    assert all(e.provenance and e.provenance[0].source_url == "http://x/y" for e in ents)


def test_empty_text_yields_nothing() -> None:
    assert extract_entities("") == []
