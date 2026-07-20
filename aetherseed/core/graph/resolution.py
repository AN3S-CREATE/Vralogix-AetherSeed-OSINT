"""Entity resolution / deduplication.

Two stages:

1. **Canonical key** — a normalised, deterministic string per entity (legal
   suffixes stripped for companies, emails lowercased, whitespace/punctuation
   collapsed). Exact key matches merge immediately.
2. **Fuzzy match** — within the same entity type, :func:`rapidfuzz` token-sort
   similarity above a threshold merges near-duplicates ("Acme Mining (Pty) Ltd"
   vs "Acme Mining Pty Ltd").
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from aetherseed.schemas import Entity, EntityType

_LEGAL_SUFFIXES = re.compile(
    r"\b(?:\(?pty\)?|ltd|limited|inc|incorporated|llc|llp|plc|cc|holdings|group|"
    r"corporation|corp|company|co)\.?\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DEFAULT_THRESHOLD = 90.0


def canonical_key(entity: Entity) -> str:
    """Return a deterministic dedup key for ``entity``.

    Examples
    --------
    >>> from aetherseed.schemas import Entity, EntityType
    >>> a = Entity(type=EntityType.COMPANY, label="Acme Mining (Pty) Ltd")
    >>> b = Entity(type=EntityType.COMPANY, label="ACME  MINING")
    >>> canonical_key(a) == canonical_key(b)
    True
    """
    label = entity.label.strip().lower()
    if entity.type is EntityType.COMPANY:
        label = _LEGAL_SUFFIXES.sub(" ", label)
    if entity.type is EntityType.ACCOUNT and "@" in label:
        return f"account:{label}"
    normalised = _NON_ALNUM.sub(" ", label).strip()
    return f"{entity.type.value}:{normalised}"


def similarity(a: Entity, b: Entity) -> float:
    """Fuzzy similarity in [0,100] between two same-type entity labels."""
    if a.type is not b.type:
        return 0.0
    return float(fuzz.token_sort_ratio(a.label.lower(), b.label.lower()))


def find_match(
    entity: Entity, candidates: list[Entity], *, threshold: float = _DEFAULT_THRESHOLD
) -> Entity | None:
    """Return the best fuzzy match for ``entity`` among ``candidates``, or ``None``.

    Only same-type candidates above ``threshold`` are considered.
    """
    best: Entity | None = None
    best_score = threshold
    for cand in candidates:
        score = similarity(entity, cand)
        if score >= best_score:
            best, best_score = cand, score
    return best


def merge_into(target: Entity, source: Entity) -> Entity:
    """Merge ``source`` into ``target`` in place and return ``target``."""
    known = {a.lower() for a in target.aliases} | {target.label.lower()}
    if source.label.lower() not in known:
        target.aliases.append(source.label)
    for alias in source.aliases:
        if alias.lower() not in known:
            target.aliases.append(alias)
    target.attributes.update({k: v for k, v in source.attributes.items() if k not in target.attributes})
    target.confidence = max(target.confidence, source.confidence)
    target.provenance.extend(source.provenance)
    return target
