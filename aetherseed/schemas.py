"""Domain schemas — the platform's public input/output contract.

These Pydantic v2 models are the stable interface between the CLI, the API, the
pipeline, and the AI engine. Everything the AetherMind engine emits is one of
these structured types (never free text), which keeps output auditable and
machine-consumable.

Contract summary
----------------
Input : :class:`SubjectSeed`
Output: :class:`InvestigationRun` (on success) or :class:`StructuredError`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aetherseed.errors import ErrorCategory


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _Base(BaseModel):
    """Shared config: forbid unknown fields to catch contract drift early."""

    model_config = ConfigDict(extra="forbid", frozen=False, use_enum_values=False)


# --- Enums -------------------------------------------------------------------


class SubjectType(StrEnum):
    PERSON = "person"
    COMPANY = "company"
    DOMAIN = "domain"
    EVENT = "event"
    CUSTOM = "custom"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"  # completed with some failed items
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SeedStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class EntityType(StrEnum):
    PERSON = "person"
    COMPANY = "company"
    DOMAIN = "domain"
    ASSET = "asset"
    TRANSACTION = "transaction"
    LOCATION = "location"
    DOCUMENT = "document"
    ACCOUNT = "account"  # social/handle/email/phone
    EVENT = "event"
    OTHER = "other"


class RelationType(StrEnum):
    OWNS = "owns"
    DIRECTOR_OF = "director_of"
    EMPLOYED_BY = "employed_by"
    ASSOCIATED_WITH = "associated_with"
    LOCATED_AT = "located_at"
    PAID = "paid"
    MENTIONS = "mentions"
    REGISTERED = "registered"
    CONTROLS = "controls"
    RELATED_TO = "related_to"


# --- Input contract ----------------------------------------------------------


class Constraints(_Base):
    """Run-scoped safety and scope limits."""

    max_depth: int = Field(default=2, ge=0, le=10)
    max_seeds: int = Field(default=50, ge=0)
    max_pages: int = Field(default=200, ge=0)
    budget_usd: float = Field(default=0.0, ge=0.0)
    require_approval: bool = True
    respect_robots: bool = True
    fail_fast_error_pct: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Abort run if failed/processed exceeds this."
    )


class SubjectSeed(_Base):
    """Primary investigation input.

    Examples
    --------
    >>> SubjectSeed(subject_type="company",
    ...             primary_identifiers=["Example Mining Pty Ltd"]).subject_type
    <SubjectType.COMPANY: 'company'>
    """

    subject_type: SubjectType
    primary_identifiers: list[str] = Field(min_length=1)
    context: str = Field(default="", max_length=4000)
    constraints: Constraints = Field(default_factory=Constraints)
    existing_graph_id: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("primary_identifiers")
    @classmethod
    def _strip_identifiers(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty primary identifier is required")
        return cleaned


# --- Provenance --------------------------------------------------------------


class Provenance(_Base):
    """Where a piece of information came from, for auditability."""

    source_url: str | None = None
    page_id: str | None = None
    seed_id: str | None = None
    extractor: str = "unknown"
    retrieved_at: datetime = Field(default_factory=_utcnow)
    content_hash: str | None = None


# --- Entities & relationships ------------------------------------------------


class Entity(_Base):
    id: str = Field(default_factory=lambda: _new_id("ent"))
    type: EntityType
    label: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    provenance: list[Provenance] = Field(default_factory=list)


class Relationship(_Base):
    id: str = Field(default_factory=lambda: _new_id("rel"))
    source_id: str
    target_id: str
    type: RelationType
    label: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    weight: float = Field(default=1.0, ge=0.0)
    provenance: list[Provenance] = Field(default_factory=list)


class GraphDelta(_Base):
    """Nodes and edges added/updated during a run."""

    nodes: list[Entity] = Field(default_factory=list)
    edges: list[Relationship] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.nodes and not self.edges


# --- Leads, gaps, actions ----------------------------------------------------


class Lead(_Base):
    """A scored, actionable finding worth pursuing."""

    id: str = Field(default_factory=lambda: _new_id("lead"))
    title: str
    summary: str = ""
    lead_type: str = "generic"  # entity | url | handle | money_trail | document | ...
    value: str = ""  # the concrete artifact (url, handle, name, ...)
    why_it_matters: str = ""
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    provenance: list[Provenance] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """Composite priority score (relevance and confidence, boosted by novelty)."""
        return round(
            0.55 * self.relevance + 0.25 * self.confidence + 0.20 * self.novelty,
            4,
        )


class NextAction(_Base):
    """A recommended next step for the investigator or the auto-seeder."""

    id: str = Field(default_factory=lambda: _new_id("act"))
    action: str  # e.g. "crawl", "enrich_whois", "search_registry"
    target: str
    rationale: str = ""
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_approval: bool = True
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class GapReport(_Base):
    """What we still do not know that we should — drives auto-seeding."""

    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_dimensions: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    recommended_actions: list[NextAction] = Field(default_factory=list)
    notes: str = ""


# --- Assets & audit ----------------------------------------------------------


class AssetRecord(_Base):
    id: str = Field(default_factory=lambda: _new_id("asset"))
    kind: Literal["screenshot", "download", "pdf", "html", "other"] = "other"
    path: str
    content_type: str | None = None
    sha256: str
    size_bytes: int = Field(ge=0)
    source_url: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class AssetManifest(_Base):
    assets: list[AssetRecord] = Field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.assets)


# --- Metrics & run -----------------------------------------------------------


class RunMetrics(_Base):
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0
    pages_fetched: int = 0
    bytes_downloaded: int = 0
    seeds_generated: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def failure_rate(self) -> float:
        return self.failed / self.processed if self.processed else 0.0


class StructuredError(_Base):
    """Machine-readable error envelope returned on failure."""

    error: str
    message: str
    category: ErrorCategory = ErrorCategory.PERMANENT
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class InvestigationRun(_Base):
    """The full output contract of an investigation run."""

    run_id: str = Field(default_factory=lambda: _new_id("run"))
    subject: SubjectSeed
    status: RunStatus = RunStatus.PENDING
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    new_leads: list[Lead] = Field(default_factory=list)
    graph_delta: GraphDelta = Field(default_factory=GraphDelta)
    gap_report: GapReport = Field(default_factory=GapReport)
    next_recommended_actions: list[NextAction] = Field(default_factory=list)
    asset_manifest: AssetManifest = Field(default_factory=AssetManifest)
    audit_log_ref: str | None = None
    errors: list[StructuredError] = Field(default_factory=list)

    def top_leads(self, n: int = 10) -> list[Lead]:
        """Return the ``n`` highest-scoring leads."""
        return sorted(self.new_leads, key=lambda x: x.score, reverse=True)[:n]
