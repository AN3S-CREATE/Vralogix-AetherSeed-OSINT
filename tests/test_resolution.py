"""Entity-resolution tests, incl. property-based checks."""

from __future__ import annotations

from aetherseed.core.graph.resolution import canonical_key, find_match, merge_into, similarity
from aetherseed.schemas import Entity, EntityType
from hypothesis import assume, given
from hypothesis import strategies as st

# ASCII letters/digits/space: avoids exotic Unicode casefold quirks (ß -> SS)
# that are out of scope for canonical-key equivalence.
_NAMES = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
    min_size=1,
    max_size=40,
)


def _company(label: str) -> Entity:
    return Entity(type=EntityType.COMPANY, label=label)


def test_legal_suffix_normalisation() -> None:
    a = _company("Acme Mining (Pty) Ltd")
    b = _company("ACME  Mining")
    assert canonical_key(a) == canonical_key(b)


def test_email_account_key() -> None:
    e = Entity(type=EntityType.ACCOUNT, label="Jane@Acme.CO.za", attributes={"kind": "email"})
    assert canonical_key(e) == "account:jane@acme.co.za"


def test_fuzzy_match_merges_near_duplicates() -> None:
    existing = [_company("Aurora Holdings Ltd")]
    match = find_match(_company("Aurora Holdings Limited"), existing)
    assert match is not None


def test_merge_accumulates_aliases_and_provenance() -> None:
    a = _company("Acme")
    b = _company("Acme Mining")
    b.confidence = 0.9
    merged = merge_into(a, b)
    assert "Acme Mining" in merged.aliases
    assert merged.confidence == 0.9


@given(_NAMES)
def test_canonical_key_case_insensitive(name: str) -> None:
    assume(name.strip())
    assert canonical_key(_company(name)) == canonical_key(_company(name.upper()))


@given(_NAMES)
def test_similarity_reflexive(name: str) -> None:
    assume(name.strip())
    assert similarity(_company(name), _company(name)) == 100.0
