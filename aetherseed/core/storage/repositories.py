"""Repositories: the only place that talks to the ORM.

Each repository wraps a :class:`~sqlalchemy.orm.Session` and exposes intention-
revealing methods. Keeping SQL here means the pipeline and services stay
persistence-agnostic and testable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aetherseed.core.storage.models import (
    AssetRow,
    Checkpoint,
    FailedItem,
    PageRecord,
    RunRecord,
    SeedDecision,
    SeedRecord,
)
from aetherseed.schemas import InvestigationRun, SeedStatus, SubjectSeed


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RunRepository:
    """CRUD for investigation runs."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def create(self, run: InvestigationRun) -> RunRecord:
        """Insert the run, or update it in place if the id already exists (resume)."""
        existing = self.s.get(RunRecord, run.run_id)
        if existing is not None:
            existing.status = run.status.value
            existing.audit_log_ref = run.audit_log_ref
            self.s.flush()
            return existing
        rec = RunRecord(
            run_id=run.run_id,
            status=run.status.value,
            subject=run.subject.model_dump(mode="json"),
            metrics=run.metrics.model_dump(mode="json"),
            graph_id=run.subject.existing_graph_id,
            audit_log_ref=run.audit_log_ref,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def get(self, run_id: str) -> RunRecord | None:
        return self.s.get(RunRecord, run_id)

    def update_status(self, run_id: str, status: str) -> None:
        rec = self.s.get(RunRecord, run_id)
        if rec is not None:
            rec.status = status

    def save_result(self, run: InvestigationRun) -> None:
        rec = self.s.get(RunRecord, run.run_id)
        if rec is None:
            self.create(run)
            return
        rec.status = run.status.value
        rec.metrics = run.metrics.model_dump(mode="json")
        rec.audit_log_ref = run.audit_log_ref

    def list_recent(self, limit: int = 50) -> list[RunRecord]:
        stmt = select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
        return list(self.s.scalars(stmt))


class SeedRepository:
    """CRUD + frontier management for seeds."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def add_from_subject(
        self,
        run_id: str,
        subject: SubjectSeed,
        *,
        origin: str = "manual",
        depth: int = 0,
        parent_seed_id: str | None = None,
        status: SeedStatus = SeedStatus.APPROVED,
        score: float = 1.0,
    ) -> SeedRecord:
        rec = SeedRecord(
            id=_uid("seed"),
            run_id=run_id,
            subject_type=subject.subject_type.value,
            identifiers=list(subject.primary_identifiers),
            context=subject.context,
            status=status.value,
            origin=origin,
            depth=depth,
            parent_seed_id=parent_seed_id,
            tags=list(subject.tags),
            score=score,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def add(
        self,
        run_id: str,
        *,
        subject_type: str,
        identifiers: list[str],
        context: str = "",
        origin: str = "auto",
        depth: int = 0,
        parent_seed_id: str | None = None,
        status: SeedStatus = SeedStatus.PENDING,
        score: float = 0.5,
        tags: list[str] | None = None,
    ) -> SeedRecord:
        rec = SeedRecord(
            id=_uid("seed"),
            run_id=run_id,
            subject_type=subject_type,
            identifiers=identifiers,
            context=context,
            status=status.value,
            origin=origin,
            depth=depth,
            parent_seed_id=parent_seed_id,
            tags=tags or [],
            score=score,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def get(self, seed_id: str) -> SeedRecord | None:
        return self.s.get(SeedRecord, seed_id)

    def next_active(self, run_id: str) -> SeedRecord | None:
        """Highest-scoring approved/active seed not yet exhausted (priority queue)."""
        stmt = (
            select(SeedRecord)
            .where(
                SeedRecord.run_id == run_id,
                SeedRecord.status.in_([SeedStatus.APPROVED.value, SeedStatus.ACTIVE.value]),
            )
            .order_by(SeedRecord.score.desc(), SeedRecord.created_at.asc())
            .limit(1)
        )
        return self.s.scalars(stmt).first()

    def set_status(self, seed_id: str, status: SeedStatus) -> None:
        rec = self.s.get(SeedRecord, seed_id)
        if rec is not None:
            rec.status = status.value

    def list_by_run(self, run_id: str, *, status: SeedStatus | None = None) -> list[SeedRecord]:
        stmt = select(SeedRecord).where(SeedRecord.run_id == run_id)
        if status is not None:
            stmt = stmt.where(SeedRecord.status == status.value)
        return list(self.s.scalars(stmt.order_by(SeedRecord.score.desc())))

    def exists_identical(self, run_id: str, subject_type: str, identifiers: list[str]) -> bool:
        """Whether an equivalent seed already exists (dedup for auto-seeding)."""
        key = sorted(i.lower().strip() for i in identifiers)
        stmt = select(SeedRecord).where(
            SeedRecord.run_id == run_id, SeedRecord.subject_type == subject_type
        )
        return any(
            sorted(i.lower().strip() for i in rec.identifiers) == key
            for rec in self.s.scalars(stmt)
        )

    def count_recent_auto(self, run_id: str, *, since: datetime) -> int:
        """Count auto-generated seeds since ``since`` (safety-budget enforcement)."""
        stmt = select(SeedRecord).where(
            SeedRecord.run_id == run_id,
            SeedRecord.origin == "auto",
            SeedRecord.created_at >= since,
        )
        return len(list(self.s.scalars(stmt)))


class PageRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def record(
        self,
        run_id: str,
        *,
        url: str,
        final_url: str | None,
        status_code: int,
        content_type: str | None,
        content_hash: str | None,
        title: str | None,
        seed_id: str | None,
        depth: int,
        rendered: bool = False,
    ) -> PageRecord:
        rec = PageRecord(
            id=_uid("page"),
            run_id=run_id,
            seed_id=seed_id,
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            content_hash=content_hash,
            title=title,
            depth=depth,
            rendered=rendered,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def seen_hash(self, run_id: str, content_hash: str) -> bool:
        """Whether a page with this content hash was already stored (dedup)."""
        stmt = select(PageRecord.id).where(
            PageRecord.run_id == run_id, PageRecord.content_hash == content_hash
        )
        return self.s.scalars(stmt).first() is not None

    def count(self, run_id: str) -> int:
        return len(list(self.s.scalars(select(PageRecord.id).where(PageRecord.run_id == run_id))))


class FailedItemRepository:
    """The dead-letter queue."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def record(
        self,
        run_id: str,
        *,
        kind: str,
        target: str | None,
        category: str,
        retryable: bool,
        error: str,
        traceback: str | None = None,
        seed_id: str | None = None,
        page_id: str | None = None,
        payload_hash: str | None = None,
        attempt: int = 1,
    ) -> FailedItem:
        rec = FailedItem(
            id=_uid("fail"),
            run_id=run_id,
            kind=kind,
            target=target,
            category=category,
            retryable=retryable,
            error=error,
            traceback=traceback,
            seed_id=seed_id,
            page_id=page_id,
            payload_hash=payload_hash,
            attempt=attempt,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def list_by_run(self, run_id: str, *, unresolved_only: bool = True) -> list[FailedItem]:
        stmt = select(FailedItem).where(FailedItem.run_id == run_id)
        if unresolved_only:
            stmt = stmt.where(FailedItem.resolved.is_(False))
        return list(self.s.scalars(stmt.order_by(FailedItem.created_at.asc())))

    def count(self, run_id: str) -> int:
        return len(list(self.s.scalars(select(FailedItem.id).where(FailedItem.run_id == run_id))))


class CheckpointRepository:
    """Resumable watermarks."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def save(self, run_id: str, key: str, watermark: dict[str, Any]) -> None:
        rec = self.s.get(Checkpoint, (run_id, key))
        if rec is None:
            rec = Checkpoint(run_id=run_id, key=key, watermark=watermark)
            self.s.add(rec)
        else:
            rec.watermark = watermark
            rec.updated_at = datetime.now(UTC)
        self.s.flush()

    def load(self, run_id: str, key: str) -> dict[str, Any] | None:
        rec = self.s.get(Checkpoint, (run_id, key))
        return dict(rec.watermark) if rec else None


class SeedDecisionRepository:
    """Audit trail of auto-seeding decisions."""

    def __init__(self, session: Session) -> None:
        self.s = session

    def record(
        self,
        run_id: str,
        *,
        decision: str,
        source: str,
        rationale: str,
        rule: str | None = None,
        seed_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SeedDecision:
        rec = SeedDecision(
            id=_uid("dec"),
            run_id=run_id,
            seed_id=seed_id,
            decision=decision,
            source=source,
            rule=rule,
            rationale=rationale,
            payload=payload or {},
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def list_by_run(self, run_id: str) -> list[SeedDecision]:
        stmt = (
            select(SeedDecision)
            .where(SeedDecision.run_id == run_id)
            .order_by(SeedDecision.created_at.asc())
        )
        return list(self.s.scalars(stmt))


class AssetRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def record(
        self,
        run_id: str,
        *,
        kind: str,
        path: str,
        sha256: str,
        size_bytes: int,
        content_type: str | None,
        source_url: str | None,
    ) -> AssetRow:
        rec = AssetRow(
            id=_uid("asset"),
            run_id=run_id,
            kind=kind,
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            source_url=source_url,
        )
        self.s.add(rec)
        self.s.flush()
        return rec

    def list_by_run(self, run_id: str) -> list[AssetRow]:
        return list(self.s.scalars(select(AssetRow).where(AssetRow.run_id == run_id)))
