"""Concrete enrichers + registry.

* :class:`DnsEnricher` — resolves a domain's A/AAAA records (functional, stdlib).
* :class:`WhoisEnricher` — **stub**: returns no facts but demonstrates the shape;
  wire a real WHOIS client (e.g. ``python-whois``) inside :meth:`enrich`.
* :class:`RegistryEnricher` — **stub** for company registries (SA CIPC-ready);
  wire an official API/dataset behind :meth:`enrich`.

``get_enrichers`` returns the enrichers enabled for a run.
"""

from __future__ import annotations

import asyncio
import socket

from aetherseed.core.interfaces import Enricher, EnrichmentResult
from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, EntityType, Provenance, Relationship, RelationType

log = get_logger(__name__)


class DnsEnricher:
    """Resolves domain entities to IP addresses (A/AAAA)."""

    name = "dns"

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.DOMAIN

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        host = entity.label.strip().lower()
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror:
            return EnrichmentResult()
        addresses = sorted({info[4][0] for info in infos})
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


class WhoisEnricher:
    """Stub WHOIS enricher — returns nothing until a provider is wired in."""

    name = "whois"

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.DOMAIN

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        log.debug("enricher.whois_stub", entity=entity.label)
        # TODO(enrichment): call a real WHOIS client and populate registrant,
        # creation date, registrar, nameservers, and emit Relationship edges.
        return EnrichmentResult(attributes={"whois": "not_configured"})


class RegistryEnricher:
    """Stub company-registry enricher (SA CIPC-ready shape)."""

    name = "registry"

    def supports(self, entity: Entity) -> bool:
        return entity.type is EntityType.COMPANY

    async def enrich(self, entity: Entity) -> EnrichmentResult:
        log.debug("enricher.registry_stub", entity=entity.label)
        # TODO(enrichment): query the company registry (directors, registration
        # number, status, addresses) and emit director_of / located_at edges.
        return EnrichmentResult(attributes={"registry": "not_configured"})


_ALL: dict[str, Enricher] = {
    e.name: e for e in (DnsEnricher(), WhoisEnricher(), RegistryEnricher())
}


def get_enrichers(names: list[str] | None = None) -> list[Enricher]:
    """Return enrichers by name (default: all). Unknown names are ignored."""
    if names is None:
        return list(_ALL.values())
    return [_ALL[n] for n in names if n in _ALL]
