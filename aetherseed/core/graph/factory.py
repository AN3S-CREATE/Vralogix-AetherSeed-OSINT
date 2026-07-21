"""Graph-store selection with local-first graceful degradation.

:func:`get_graph_store` returns the configured backend as the ``GraphStore``
Protocol. The default is the in-memory NetworkX store; ``neo4j`` is used only
when configured *and* reachable, otherwise the platform degrades to NetworkX and
logs the reason — never hard-failing for an absent optional service.

Note
----
Follow-the-money analysis (:mod:`aetherseed.core.graph.money`) operates on the
concrete NetworkX store's in-memory graph. The Neo4j backend is for durable,
queryable persistence of the resolved graph; the pipeline mirrors each run's
delta into it when ``AETHERSEED_GRAPH_BACKEND=neo4j``.
"""

from __future__ import annotations

from aetherseed.config import Settings, get_settings
from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.core.interfaces import GraphStore
from aetherseed.logging import get_logger

log = get_logger(__name__)


def get_graph_store(
    settings: Settings | None = None, *, graph_id: str | None = None
) -> GraphStore:
    """Return the configured graph store (NetworkX by default).

    Parameters
    ----------
    settings:
        Application settings; ``graph_backend`` selects the implementation.
    graph_id:
        Optional existing graph id (only meaningful for the in-memory store).

    Examples
    --------
    >>> store = get_graph_store()
    >>> store.__class__.__name__
    'NetworkXGraphStore'
    """
    s = settings or get_settings()
    if s.graph_backend == "neo4j":
        try:
            from aetherseed.core.graph.neo4j_store import Neo4jGraphStore

            store = Neo4jGraphStore(s)
            if store.available():
                log.info("graph.backend_selected", backend="neo4j", uri=s.neo4j_uri)
                return store
            store.close()
            log.warning("graph.neo4j_unreachable", uri=s.neo4j_uri, fallback="networkx")
        except Exception as exc:  # import/config/connection failure -> degrade
            log.warning("graph.neo4j_init_failed", error=str(exc), fallback="networkx")
    return NetworkXGraphStore(graph_id=graph_id)
