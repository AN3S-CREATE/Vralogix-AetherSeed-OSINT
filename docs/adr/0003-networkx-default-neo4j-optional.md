# ADR 0003 — NetworkX default graph, Neo4j optional

- Status: Accepted
- Date: 2026-07-20

## Context

The knowledge graph needs path finding, centrality, community detection, and
follow-the-money queries, plus easy export (GraphML/JSON-LD/Cytoscape). Neo4j
(or Apache AGE) offers durable, queryable graphs at scale but adds an external
service and operational weight that conflicts with local-first defaults.

## Decision

Use **NetworkX in-memory** as the default `GraphStore`, with durable persistence
of nodes/edges to the relational DB and rich exports. Provide a **Neo4j**
implementation of the same protocol as an optional (`graph` extra) backend for
persistent, large, or multi-user graphs.

## Consequences

- Zero-config graph analytics that target 1k+ seeds / 10k+ pages on a single
  workstation.
- Follow-the-money algorithms (chains, centrality, cycles) use NetworkX directly.
- Teams needing a shared, persistent graph switch the backend via config without
  changing pipeline code.
