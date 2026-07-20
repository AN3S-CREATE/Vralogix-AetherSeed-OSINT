"""Pluggable enrichment modules.

Each enricher implements the :class:`~aetherseed.core.interfaces.Enricher`
protocol and adds external context to an entity. A working DNS enricher ships in
the box; WHOIS, certificate-transparency, and company-registry (CIPC-ready)
enrichers are provided as clearly-marked stubs with the right shape so real
providers can be dropped in without touching the pipeline.

Enrichment is opt-in (it makes outbound requests) and every enricher is subject
to the same SSRF/rate policies as acquisition.
"""

from __future__ import annotations
