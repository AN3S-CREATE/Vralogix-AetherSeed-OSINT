"""Enricher tests: DNS (stdlib), WHOIS/RDAP + registry (respx, no network)."""

from __future__ import annotations

import httpx
import pytest
import respx
from aetherseed.config import Settings
from aetherseed.core.enrichment import enrichers as enr_mod
from aetherseed.core.enrichment.enrichers import (
    DnsEnricher,
    RegistryEnricher,
    WhoisEnricher,
    get_enrichers,
)
from aetherseed.schemas import Entity, EntityType, RelationType


@pytest.fixture
def _no_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enr_mod, "resolve_and_validate", lambda url, s: None)


async def test_dns_enricher_resolves_localhost() -> None:
    ent = Entity(type=EntityType.DOMAIN, label="localhost")
    result = await DnsEnricher(Settings()).enrich(ent)
    assert result.attributes.get("resolved_ips")
    assert any(e.attributes.get("kind") == "ip_address" for e in result.entities)


_RDAP = {
    "events": [
        {"eventAction": "registration", "eventDate": "2000-01-01"},
        {"eventAction": "expiration", "eventDate": "2030-01-01"},
    ],
    "entities": [
        {"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Test Registrar"]]]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", "Acme Org"]]]},
    ],
    "nameservers": [{"ldhName": "NS1.EXAMPLE.COM"}, {"ldhName": "ns2.example.com"}],
    "status": ["client transfer prohibited"],
}


@respx.mock
async def test_whois_rdap(_no_ssrf: None) -> None:
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, json=_RDAP)
    )
    ent = Entity(type=EntityType.DOMAIN, label="example.com")
    result = await WhoisEnricher(Settings()).enrich(ent)
    assert result.attributes["registrar"] == "Test Registrar"
    assert result.attributes["registrant"] == "Acme Org"
    assert "ns1.example.com" in result.attributes["nameservers"]  # lowercased
    labels = {e.label for e in result.entities}
    assert {"ns1.example.com", "Acme Org"} <= labels


@respx.mock
async def test_whois_404_is_graceful(_no_ssrf: None) -> None:
    respx.get("https://rdap.org/domain/missing.example").mock(return_value=httpx.Response(404))
    result = await WhoisEnricher(Settings()).enrich(Entity(type=EntityType.DOMAIN, label="missing.example"))
    assert result.attributes.get("whois") == "not_found"


async def test_registry_not_configured_without_token() -> None:
    ent = Entity(type=EntityType.COMPANY, label="Acme Ltd")
    result = await RegistryEnricher(Settings(opencorporates_api_token=None)).enrich(ent)
    assert result.attributes.get("registry") == "not_configured"


@respx.mock
async def test_registry_opencorporates(_no_ssrf: None) -> None:
    respx.get("https://api.opencorporates.com/v0.4/companies/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "companies": [
                        {
                            "company": {
                                "name": "Acme Mining Ltd",
                                "company_number": "12345",
                                "jurisdiction_code": "za",
                                "current_status": "Active",
                                "opencorporates_url": "https://opencorporates.com/companies/za/12345",
                                "officers": [{"officer": {"name": "Jane Doe", "position": "director"}}],
                            }
                        }
                    ]
                }
            },
        )
    )
    ent = Entity(type=EntityType.COMPANY, label="Acme Mining Ltd")
    result = await RegistryEnricher(Settings(opencorporates_api_token="tok")).enrich(ent)
    assert result.attributes["company_number"] == "12345"
    assert result.attributes["jurisdiction"] == "za"
    assert any(e.label == "Jane Doe" and e.type is EntityType.PERSON for e in result.entities)
    assert any(r.type is RelationType.DIRECTOR_OF for r in result.relationships)


def test_get_enrichers_default_set() -> None:
    names = {e.name for e in get_enrichers(Settings())}
    assert names == {"dns", "whois", "registry"}
