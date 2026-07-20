"""Veralogix AetherSeed OSINT.

A local-first, resilient, auditable investigative research platform.

The package is layered so that any component (scraper, enricher, LLM backend,
graph store) can be swapped through the Protocols in :mod:`aetherseed.core.interfaces`.
Nothing here requires a network service to import: optional integrations
(Ollama, Playwright, Redis, Postgres, Neo4j) are imported lazily and degrade
gracefully when their dependencies are absent.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
