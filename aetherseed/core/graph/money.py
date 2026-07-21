"""Follow-the-money analysis over the knowledge graph.

Operates on a :class:`~aetherseed.core.graph.store.NetworkXGraphStore` and
surfaces the structures investigators care about:

* **Ownership / control chains** — directed paths along ``owns`` / ``controls``
  / ``director_of`` edges (who ultimately controls whom).
* **Director networks** — shared-director links between companies (a classic
  proxy for hidden association).
* **Payment flows** — ``paid`` edges as a money-movement view.
* **Red-flag scoring** — heuristic risk signals (circular ownership, unusually
  central intermediaries, shell-like nodes, dense director overlap).

Everything here is *hypothesis-generating*, not proof — scores and chains are
leads for a human to verify, and every claim is traceable to its provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import networkx as nx
from dateutil import parser as _dateparser

from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.schemas import EntityType, RelationType

_CONTROL_RELS = {RelationType.OWNS.value, RelationType.CONTROLS.value, RelationType.DIRECTOR_OF.value}
_MONEY_RELS = {RelationType.PAID.value}

# Attribute keys that plausibly carry a date/timestamp, in priority order.
_DATE_KEYS = (
    "date", "timestamp", "occurred_at", "filed_at", "registered_at",
    "registered", "incorporated", "incorporation_date", "founded",
)
_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lon", "lng", "longitude")


def _parse_date(value: Any) -> datetime | None:
    """Best-effort parse of a value into a datetime; ``None`` if not date-like."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _dateparser.parse(value)
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class OwnershipChain:
    path: list[str]  # entity ids, controller -> ... -> controlled
    labels: list[str]
    relations: list[str]

    @property
    def depth(self) -> int:
        return len(self.path) - 1


@dataclass(slots=True)
class RedFlag:
    entity_id: str
    label: str
    signal: str
    severity: float  # 0..1
    detail: str = ""


@dataclass(slots=True)
class MoneyReport:
    ownership_chains: list[OwnershipChain] = field(default_factory=list)
    shared_directors: list[dict[str, Any]] = field(default_factory=list)
    payment_flows: list[dict[str, Any]] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    geo: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ownership_chains": [
                {"path": c.path, "labels": c.labels, "relations": c.relations, "depth": c.depth}
                for c in self.ownership_chains
            ],
            "shared_directors": self.shared_directors,
            "payment_flows": self.payment_flows,
            "red_flags": [
                {
                    "entity_id": f.entity_id,
                    "label": f.label,
                    "signal": f.signal,
                    "severity": round(f.severity, 3),
                    "detail": f.detail,
                }
                for f in sorted(self.red_flags, key=lambda x: x.severity, reverse=True)
            ],
            "timeline": self.timeline,
            "geo": self.geo,
        }


class FollowTheMoney:
    """Follow-the-money analyser bound to a graph store."""

    def __init__(self, store: NetworkXGraphStore) -> None:
        self.store = store
        self.g = store.graph
        # Per-analyser caches: the underlying graph is treated as immutable for
        # the analyser's lifetime, so expensive derived structures are built once.
        self._ctrl: nx.DiGraph | None = None
        self._betw: dict[str, float] | None = None

    def _label(self, node_id: str) -> str:
        ent = self.store.get_entity(node_id)
        return ent.label if ent else node_id

    def _control_subgraph(self) -> nx.DiGraph:
        if self._ctrl is None:
            h = nx.DiGraph()
            for u, v, data in self.g.edges(data=True):
                if data.get("type") in _CONTROL_RELS:
                    h.add_edge(u, v, type=data.get("type"))
            self._ctrl = h
        return self._ctrl

    def _betweenness(self) -> dict[str, float]:
        """Betweenness centrality over the undirected graph (cached)."""
        if self._betw is None:
            if self.g.number_of_nodes() < 3:
                self._betw = {}
            else:
                self._betw = nx.betweenness_centrality(self.g.to_undirected(as_view=True))
        return self._betw

    def ownership_chains(self, *, max_depth: int = 6) -> list[OwnershipChain]:
        """All control chains (roots -> leaves) in the ownership/control subgraph."""
        ctrl = self._control_subgraph()
        if ctrl.number_of_edges() == 0:
            return []
        roots = [n for n in ctrl.nodes if ctrl.in_degree(n) == 0]
        leaves = [n for n in ctrl.nodes if ctrl.out_degree(n) == 0]
        chains: list[OwnershipChain] = []
        for root in roots:
            for leaf in leaves:
                if root == leaf:
                    continue
                for path in nx.all_simple_paths(ctrl, root, leaf, cutoff=max_depth):
                    relations = [
                        ctrl.edges[path[i], path[i + 1]].get("type", "?")
                        for i in range(len(path) - 1)
                    ]
                    chains.append(
                        OwnershipChain(
                            path=path,
                            labels=[self._label(n) for n in path],
                            relations=relations,
                        )
                    )
        return chains

    def shared_directors(self) -> list[dict[str, Any]]:
        """Company pairs linked by a common director (hidden-association signal)."""
        companies_by_director: dict[str, list[str]] = {}
        for u, v, data in self.g.edges(data=True):
            if data.get("type") == RelationType.DIRECTOR_OF.value:
                companies_by_director.setdefault(u, []).append(v)
        out: list[dict[str, Any]] = []
        for director, companies in companies_by_director.items():
            uniq = sorted(set(companies))
            if len(uniq) >= 2:
                out.append(
                    {
                        "director_id": director,
                        "director": self._label(director),
                        "companies": [self._label(c) for c in uniq],
                        "company_ids": uniq,
                    }
                )
        return out

    def payment_flows(self) -> list[dict[str, Any]]:
        """All ``paid`` edges as a money-movement list."""
        flows: list[dict[str, Any]] = []
        for u, v, data in self.g.edges(data=True):
            if data.get("type") in _MONEY_RELS:
                flows.append(
                    {
                        "from": self._label(u),
                        "to": self._label(v),
                        "from_id": u,
                        "to_id": v,
                        "amount": data.get("attributes", {}).get("amount"),
                        "confidence": data.get("confidence"),
                    }
                )
        return flows

    def red_flags(self) -> list[RedFlag]:
        """Heuristic risk signals across the graph."""
        flags: list[RedFlag] = []
        ctrl = self._control_subgraph()

        # 1. Circular ownership.
        try:
            for cycle in nx.simple_cycles(ctrl):
                if len(cycle) >= 2:
                    flags.append(
                        RedFlag(
                            entity_id=cycle[0],
                            label=self._label(cycle[0]),
                            signal="circular_ownership",
                            severity=0.9,
                            detail=" -> ".join(self._label(n) for n in cycle),
                        )
                    )
        except nx.NetworkXNoCycle:
            pass

        # 2. Highly central intermediaries (potential conduits).
        if self.g.number_of_nodes() >= 3:
            betw = self._betweenness()
            threshold = 0.25
            for nid, score in betw.items():
                if score >= threshold:
                    flags.append(
                        RedFlag(
                            entity_id=nid,
                            label=self._label(nid),
                            signal="central_intermediary",
                            severity=min(1.0, score),
                            detail=f"betweenness={score:.2f}",
                        )
                    )

        # 3. Shell-like companies: owned/controlled but no other descriptive edges.
        for nid, ent in ((n, self.store.get_entity(n)) for n in self.g.nodes):
            if ent is None or ent.type is not EntityType.COMPANY:
                continue
            incoming_control = any(
                self.g.edges[u, nid, k].get("type") in _CONTROL_RELS
                for u, _, k in self.g.in_edges(nid, keys=True)
            )
            degree = self.g.degree(nid)
            if incoming_control and degree <= 1 and not ent.attributes:
                flags.append(
                    RedFlag(
                        entity_id=nid,
                        label=ent.label,
                        signal="possible_shell",
                        severity=0.55,
                        detail="controlled entity with no independent footprint",
                    )
                )

        # 4. Dense director overlap.
        for row in self.shared_directors():
            if len(row["companies"]) >= 3:
                flags.append(
                    RedFlag(
                        entity_id=row["director_id"],
                        label=row["director"],
                        signal="director_hub",
                        severity=0.6,
                        detail=f"director of {len(row['companies'])} companies",
                    )
                )
        return flags

    def timeline(self) -> list[dict[str, Any]]:
        """A chronological view of dated entities (transactions, filings, events).

        Scans every entity for a date-like attribute (see ``_DATE_KEYS``) and, for
        transactions, surfaces the movement. Returns events sorted oldest-first;
        entries with no parseable date are omitted. This powers timeline
        visualisations and temporal-pattern review without requiring a model.
        """
        events: list[tuple[datetime, dict[str, Any]]] = []
        for nid in self.g.nodes:
            ent = self.store.get_entity(nid)
            if ent is None:
                continue
            when: datetime | None = None
            for key in _DATE_KEYS:
                if key in ent.attributes and (when := _parse_date(ent.attributes[key])) is not None:
                    break
            if when is None:
                continue
            events.append(
                (
                    when,
                    {
                        "date": when.isoformat(),
                        "entity_id": nid,
                        "label": ent.label,
                        "kind": ent.type.value,
                        "amount": ent.attributes.get("amount"),
                    },
                )
            )
        events.sort(key=lambda e: (e[0], e[1]["label"]))
        return [payload for _, payload in events]

    def geo_points(self) -> list[dict[str, Any]]:
        """Entities carrying coordinates, for map overlays (empty if none)."""
        points: list[dict[str, Any]] = []
        for nid in self.g.nodes:
            ent = self.store.get_entity(nid)
            if ent is None:
                continue
            lat = next((_parse_float(ent.attributes[k]) for k in _LAT_KEYS if k in ent.attributes), None)
            lon = next((_parse_float(ent.attributes[k]) for k in _LON_KEYS if k in ent.attributes), None)
            if lat is None or lon is None:
                continue
            points.append(
                {
                    "entity_id": nid,
                    "label": ent.label,
                    "kind": ent.type.value,
                    "lat": lat,
                    "lon": lon,
                }
            )
        return points

    def analyze(self, *, max_depth: int = 6) -> MoneyReport:
        """Run the full follow-the-money analysis."""
        return MoneyReport(
            ownership_chains=self.ownership_chains(max_depth=max_depth),
            shared_directors=self.shared_directors(),
            payment_flows=self.payment_flows(),
            red_flags=self.red_flags(),
            timeline=self.timeline(),
            geo=self.geo_points(),
        )
