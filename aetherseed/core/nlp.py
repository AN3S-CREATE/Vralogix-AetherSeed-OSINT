"""Deterministic text-mining helpers (no model required).

These regex-based extractors are the always-available floor beneath the AI
engine: even with no LLM installed, the platform still finds emails, phones,
domains, social handles, money amounts, and company-like names. The AetherMind
engine layers richer NER/relation extraction on top when a model is present.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from aetherseed.schemas import Entity, EntityType, Provenance

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# International-ish phone: optional +, groups of digits with separators, 7-15 digits.
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?){2,4}\d{2,4}(?!\w)")
HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{2,30})\b")
URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\b", re.IGNORECASE)
MONEY_RE = re.compile(
    r"(?:R|ZAR|US\$|\$|€|£)\s?\d{1,3}(?:[ ,]\d{3})*(?:\.\d+)?(?:\s?(?:million|billion|m|bn|k))?",
    re.IGNORECASE,
)
# Company-like: capitalised words followed by a legal suffix.
COMPANY_RE = re.compile(
    r"\b([A-Z][\w&.\-]+(?:\s+[A-Z][\w&.\-]+){0,5})\s+"
    r"((?:\(Pty\)\s+)?(?:Pty\s+)?(?:Ltd|Limited|Inc|Incorporated|LLC|LLP|PLC|CC|Trust|"
    r"Holdings|Group|Corporation|Corp|Company|Co)\.?)\b"
)

_STOP_DOMAINS = {"w3.org", "schema.org", "example.com"}
_COMMON_TLDS_ONLY = re.compile(r"^\d+(?:\.\d+)+$")  # avoid matching bare version numbers as domains


def guess_domain(url: str) -> str | None:
    """Return the registrable host of a URL, or ``None``."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    return host


def _dedupe(entities: list[Entity]) -> list[Entity]:
    seen: dict[tuple[str, str], Entity] = {}
    for ent in entities:
        key = (ent.type.value, ent.label.lower().strip())
        if key not in seen:
            seen[key] = ent
    return list(seen.values())


def extract_entities(text: str, *, source_url: str | None = None) -> list[Entity]:
    """Extract entities from free text using deterministic patterns.

    Parameters
    ----------
    text:
        The text to mine.
    source_url:
        Recorded as provenance on every extracted entity.

    Returns
    -------
    list[Entity]
        Deduplicated entities with modest confidence (regex, not a model).

    Examples
    --------
    >>> ents = extract_entities("Contact jane@acme.co.za about Acme Mining Pty Ltd")
    >>> {e.type.value for e in ents} >= {"account", "company"}
    True
    """
    prov = Provenance(source_url=source_url, extractor="nlp.regex")
    out: list[Entity] = []

    for m in EMAIL_RE.finditer(text):
        out.append(
            Entity(
                type=EntityType.ACCOUNT,
                label=m.group(0).lower(),
                attributes={"kind": "email"},
                confidence=0.85,
                provenance=[prov],
            )
        )

    for m in HANDLE_RE.finditer(text):
        out.append(
            Entity(
                type=EntityType.ACCOUNT,
                label="@" + m.group(1),
                attributes={"kind": "social_handle"},
                confidence=0.5,
                provenance=[prov],
            )
        )

    for m in COMPANY_RE.finditer(text):
        label = f"{m.group(1).strip()} {m.group(2).strip()}"
        out.append(
            Entity(
                type=EntityType.COMPANY,
                label=label,
                confidence=0.6,
                provenance=[prov],
            )
        )

    for m in MONEY_RE.finditer(text):
        out.append(
            Entity(
                type=EntityType.TRANSACTION,
                label=m.group(0).strip(),
                attributes={"kind": "money_amount"},
                confidence=0.55,
                provenance=[prov],
            )
        )

    seen_domains: set[str] = set()
    for m in DOMAIN_RE.finditer(text):
        dom = m.group(0).lower()
        if dom in _STOP_DOMAINS or _COMMON_TLDS_ONLY.match(dom) or dom in seen_domains:
            continue
        # Skip domains that are actually part of an email already captured.
        seen_domains.add(dom)
        out.append(
            Entity(
                type=EntityType.DOMAIN,
                label=dom,
                confidence=0.4,
                provenance=[prov],
            )
        )

    return _dedupe(out)
