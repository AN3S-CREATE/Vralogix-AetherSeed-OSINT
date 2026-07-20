"""Concrete enrichers + registry.

* :class:`DnsEnricher` — resolves a domain's A/AAAA records (stdlib).
* :class:`WhoisEnricher` — **real** WHOIS via RDAP (RFC 9083) over HTTP: no API
  key, structured JSON. Extracts registrar, registrant org, key dates, statuses,
  and nameservers, emitting nameserver nodes + edges.
* :class:`RegistryEnricher` — **real** company registry via the OpenCorporates
  API (which includes South African CIPC data). Gated behind
  ``OPENCORPORATES_API_TOKEN``; returns ``not_configured`` without it. Emits
  officers as people with ``director_of`` edges.

All network enrichers route through the SSRF guard and honour the configured
user-agent / proxy / timeout. ``get_enrichers`` returns the enabled set.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx

from aetherseed.config import Settings, get_settings
from aetherseed.core.acquisition.security import resolve_and_validate
from aetherseed.core.interfaces import Enricher, EnrichmentResult
from aetherseed.errors import PolicyError
from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, EntityType, Provenance, Relationship, RelationType

log = get_logger(__name__)


def _client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.acq_request_timeout_s,
        headers={"User-Agent": settings.acq_user_agent, "Accept": "application/json"},
        proxy=settings.acq_proxy_url,
        follow_redirects=True,
    )


class DnsEnricher:
    """Resolves domain entities to IP addresses (A/AAAA)."""

    name = "dns"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.DOMAIN

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        host = entity.label.strip().lower()
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror:
            return EnrichmentResult()
        addresses = sorted({str(info[4][0]) for info in infos})
        prov = Provenance(source_url=f"dns://{host}", extractor="enricher.dns")
        new_entities = [
            Entity(
                type=EntityType.ASSET,
                label=addr,
                attributes={"kind": "ip_address"},
                confidence=0.9,
                provenance=[prov],
            )
            for addr in addresses
        ]
        rels = [
            Relationship(
                source_id=entity.id,
                target_id=e.id,
                type=RelationType.REGISTERED,
                label="resolves_to",
                confidence=0.9,
                provenance=[prov],
            )
            for e in new_entities
        ]
        return EnrichmentResult(
            entities=new_entities,
            relationships=rels,
            attributes={"resolved_ips": addresses},
            provenance=prov,
        )


def _vcard_value(entity: dict[str, Any], field: str) -> str | None:
    """Pull a field (e.g. ``fn``, ``org``) from an RDAP jCard/vCard array."""
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if isinstance(item, list) and item and item[0] == field and len(item) >= 4:
            value = item[3]
            return value if isinstance(value, str) else None
    return None


class WhoisEnricher:
    """WHOIS-over-RDAP enricher for domains (keyless, structured)."""

    name = "whois"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.DOMAIN

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        domain = entity.label.strip().lower()
        url = f"{self._settings.rdap_base_url.rstrip('/')}/domain/{domain}"
        try:
            resolve_and_validate(url, self._settings)
        except PolicyError:
            return EnrichmentResult()
        try:
            async with _client(self._settings) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return EnrichmentResult(attributes={"whois": "not_found"})
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("enricher.whois_failed", domain=domain, error=str(exc))
            return EnrichmentResult()

        return self._map(entity, data, url)

    def _map(self, entity: Entity, data: dict[str, Any], url: str) -> EnrichmentResult:
        prov = Provenance(source_url=url, extractor="enricher.whois_rdap")
        events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", [])}
        registrar = None
        registrant = None
        for ent in data.get("entities", []):
            roles = ent.get("roles", [])
            if "registrar" in roles and registrar is None:
                registrar = _vcard_value(ent, "fn") or _vcard_value(ent, "org")
            if "registrant" in roles and registrant is None:
                registrant = _vcard_value(ent, "org") or _vcard_value(ent, "fn")

        nameservers = [
            ns.get("ldhName", "").lower() for ns in data.get("nameservers", []) if ns.get("ldhName")
        ]
        attributes = {
            "registrar": registrar,
            "registrant": registrant,
            "registered": events.get("registration"),
            "expires": events.get("expiration"),
            "last_changed": events.get("last changed"),
            "statuses": data.get("status", []),
            "nameservers": nameservers,
        }

        new_entities: list[Entity] = []
        rels: list[Relationship] = []
        for ns in nameservers:
            ns_ent = Entity(
                type=EntityType.DOMAIN,
                label=ns,
                attributes={"kind": "nameserver"},
                confidence=0.8,
                provenance=[prov],
            )
            new_entities.append(ns_ent)
            rels.append(
                Relationship(
                    source_id=entity.id,
                    target_id=ns_ent.id,
                    type=RelationType.REGISTERED,
                    label="nameserver",
                    confidence=0.8,
                    provenance=[prov],
                )
            )
        if registrant:
            org = Entity(
                type=EntityType.COMPANY, label=registrant, confidence=0.6, provenance=[prov]
            )
            new_entities.append(org)
            rels.append(
                Relationship(
                    source_id=org.id,
                    target_id=entity.id,
                    type=RelationType.REGISTERED,
                    label="registrant_of",
                    confidence=0.6,
                    provenance=[prov],
                )
            )
        return EnrichmentResult(
            entities=new_entities, relationships=rels, attributes=attributes, provenance=prov
        )


class RegistryEnricher:
    """Company-registry enricher via OpenCorporates (SA/CIPC-ready).

    OpenCorporates aggregates official registries including South Africa's CIPC.
    Set ``OPENCORPORATES_API_TOKEN`` to enable; without it this reports
    ``not_configured`` (no fabricated data).
    """

    name = "registry"
    _SEARCH = "https://api.opencorporates.com/v0.4/companies/search"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.COMPANY

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        token = self._settings.opencorporates_api_token
        if not token:
            return EnrichmentResult(attributes={"registry": "not_configured"})
        try:
            resolve_and_validate(self._SEARCH, self._settings)
        except PolicyError:
            return EnrichmentResult()

        try:
            async with _client(self._settings) as client:
                resp = await client.get(
                    self._SEARCH, params={"q": entity.label, "api_token": token}
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("enricher.registry_failed", company=entity.label, error=str(exc))
            return EnrichmentResult()

        companies = data.get("results", {}).get("companies", [])
        if not companies:
            return EnrichmentResult(attributes={"registry": "no_match"})
        company = companies[0].get("company", {})
        prov = Provenance(
            source_url=company.get("opencorporates_url", self._SEARCH),
            extractor="enricher.opencorporates",
        )
        attributes = {
            "company_number": company.get("company_number"),
            "jurisdiction": company.get("jurisdiction_code"),
            "status": company.get("current_status"),
            "incorporation_date": company.get("incorporation_date"),
            "company_type": company.get("company_type"),
        }

        new_entities: list[Entity] = []
        rels: list[Relationship] = []
        for officer_wrap in company.get("officers", []):
            officer = officer_wrap.get("officer", {})
            name = officer.get("name")
            if not name:
                continue
            person = Entity(
                type=EntityType.PERSON,
                label=name,
                attributes={"position": officer.get("position", "")},
                confidence=0.75,
                provenance=[prov],
            )
            new_entities.append(person)
            rels.append(
                Relationship(
                    source_id=person.id,
                    target_id=entity.id,
                    type=RelationType.DIRECTOR_OF,
                    label=officer.get("position") or "officer",
                    confidence=0.75,
                    provenance=[prov],
                )
            )
        return EnrichmentResult(
            entities=new_entities, relationships=rels, attributes=attributes, provenance=prov
        )


def get_enrichers(
    settings: Settings | None = None, names: list[str] | None = None
) -> list[Enricher]:
    """Return enrichers by name (default: all), constructed with ``settings``."""
    s = settings or get_settings()
    available: dict[str, Enricher] = {
        e.name: e for e in (DnsEnricher(s), WhoisEnricher(s), RegistryEnricher(s))
    }
    if names is None:
        return list(available.values())
    return [available[n] for n in names if n in available]
