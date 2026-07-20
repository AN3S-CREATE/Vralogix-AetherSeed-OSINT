"""SQLAlchemy 2.0 ORM models — the durable backbone of the platform.

Design notes
------------
* Every table carries the ``run_id`` correlation key for per-run isolation.
* ``FailedItem`` is the dead-letter record: a failure never aborts a run, it is
  captured here with full context (url, payload hash, category, traceback,
  attempt count) so it can be inspected and replayed.
* ``Checkpoint`` stores a resumable watermark per run so a crashed or cancelled
  job can continue exactly where it stopped.
* JSON columns use the portable :class:`sqlalchemy.JSON` type (works on both
  SQLite and Postgres).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class RunRecord(Base, TimestampMixin):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    graph_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audit_log_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)

    seeds: Mapped[list[SeedRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SeedRecord(Base, TimestampMixin):
    __tablename__ = "seeds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    identifiers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    context: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto
    depth: Mapped[int] = mapped_column(Integer, default=0)
    parent_seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1)

    run: Mapped[RunRecord] = relationship(back_populates="seeds")

    __table_args__ = (Index("ix_seeds_run_status", "run_id", "status"),)


class PageRecord(Base, TimestampMixin):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered: Mapped[bool] = mapped_column(default=False)
    depth: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_pages_run_hash", "run_id", "content_hash"),)


class EntityRecord(Base, TimestampMixin):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(Text)
    canonical_key: Mapped[str] = mapped_column(String(256), index=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    provenance: Mapped[list[Any]] = mapped_column(JSON, default=list)


class RelationshipRecord(Base, TimestampMixin):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[list[Any]] = mapped_column(JSON, default=list)


class AssetRow(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="other")
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class FailedItem(Base, TimestampMixin):
    """Dead-letter record. One failed unit of work with full replay context."""

    __tablename__ = "failed_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="fetch")  # fetch|extract|enrich|llm|seed
    target: Mapped[str | None] = mapped_column(Text, nullable=True)  # url / identifier
    category: Mapped[str] = mapped_column(String(16), default="permanent")
    retryable: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    resolved: Mapped[bool] = mapped_column(default=False)


class Checkpoint(Base, TimestampMixin):
    """Resumable watermark. Keyed by (run_id, key)."""

    __tablename__ = "checkpoints"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    watermark: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SeedDecision(Base, TimestampMixin):
    """Audit record of every auto-seeding decision (why a seed was/ wasn't made)."""

    __tablename__ = "seed_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seed_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(32))  # proposed|approved|rejected|blocked
    source: Mapped[str] = mapped_column(String(32), default="rule")  # rule|llm|human
    rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


ALL_MODELS = [
    RunRecord,
    SeedRecord,
    PageRecord,
    EntityRecord,
    RelationshipRecord,
    AssetRow,
    FailedItem,
    Checkpoint,
    SeedDecision,
]
