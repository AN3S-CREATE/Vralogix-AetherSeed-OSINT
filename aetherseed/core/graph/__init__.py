"""Knowledge graph, entity resolution, and follow-the-money analysis.

The default store is in-memory NetworkX (zero-config, exportable). A persistent
Neo4j backend can be added behind the same :class:`~aetherseed.core.interfaces.
GraphStore` protocol. Entity resolution (deterministic keys + fuzzy matching)
runs on every insert so the graph converges on canonical nodes rather than
accumulating duplicates.
"""

from __future__ import annotations
