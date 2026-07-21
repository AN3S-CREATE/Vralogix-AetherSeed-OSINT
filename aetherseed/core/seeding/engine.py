"""The auto-seeding engine.

Combines rule-based, LLM, and gap-driven candidates; de-duplicates them (against
each other and the existing seed store); enforces the :class:`~aetherseed.core.
seeding.budget.SafetyBudget`; applies the human-in-the-loop approval gate; and
records every decision to both the ``seed_decisions`` table and the append-only
audit log. Nothing is created silently — the full provenance of each seed's
existence is recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from aetherseed.core.ai.schemas import LLMSeedExpansion
from aetherseed.core.seeding.budget import SafetyBudget
from aetherseed.core.seeding.rules import SeedCandidate, SeedRule, apply_rules
from aetherseed.core.storage.audit import AuditLog
from aetherseed.core.storage.repositories import SeedDecisionRepository, SeedRepository
from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, GapReport, SeedStatus, SubjectSeed, SubjectType

log = get_logger(__name__)


@dataclass(slots=True)
class SeedingOutcome:
    created_seed_ids: list[str] = field(default_factory=list)
    approved: int = 0
    pending: int = 0
    blocked: int = 0
    rejected_duplicate: int = 0

    @property
    def total_created(self) -> int:
        return len(self.created_seed_ids)


def _coerce_subject_type(raw: str) -> SubjectType:
    try:
        return SubjectType(raw.strip().lower())
    except ValueError:
        return SubjectType.CUSTOM


class SeedingEngine:
    """Generates, gates, and persists new seeds under safety budgets."""

    def __init__(
        self,
        budget: SafetyBudget | None = None,
        *,
        rules: list[SeedRule] | None = None,
    ) -> None:
        self.budget = budget or SafetyBudget()
        self.rules = rules

    def _gather(
        self,
        subject: SubjectSeed,
        entities: list[Entity],
        expansion: LLMSeedExpansion | None,
        gap: GapReport | None,
    ) -> list[SeedCandidate]:
        candidates = apply_rules(entities, subject, rules=self.rules)

        if expansion is not None:
            for c in expansion.candidate_seeds:
                candidates.append(
                    SeedCandidate(
                        subject_type=_coerce_subject_type(c.subject_type),
                        identifiers=list(c.identifiers),
                        context=c.rationale or "LLM-proposed seed.",
                        priority=c.priority,
                        rationale=c.rationale or "Proposed by AetherMind seed expansion.",
                        source="llm",
                    )
                )

        if gap is not None:
            for action in gap.recommended_actions:
                if action.action == "crawl" and action.target:
                    candidates.append(
                        SeedCandidate(
                            subject_type=SubjectType.DOMAIN
                            if "." in action.target
                            else SubjectType.CUSTOM,
                            identifiers=[action.target],
                            context=action.rationale or "Gap-driven crawl target.",
                            priority=action.priority,
                            rationale=f"Gap analysis: {action.rationale}",
                            source="gap",
                        )
                    )

        # De-duplicate candidate set, keeping the highest-priority instance.
        best: dict[tuple[str, tuple[str, ...]], SeedCandidate] = {}
        for cand in sorted(candidates, key=lambda c: c.priority, reverse=True):
            best.setdefault(cand.dedup_key(), cand)
        return sorted(best.values(), key=lambda c: c.priority, reverse=True)

    def propose(
        self,
        run_id: str,
        subject: SubjectSeed,
        entities: list[Entity],
        session: Session,
        audit: AuditLog,
        *,
        expansion: LLMSeedExpansion | None = None,
        gap: GapReport | None = None,
        depth: int = 0,
        parent_seed_id: str | None = None,
        max_new: int | None = None,
    ) -> SeedingOutcome:
        """Propose, gate, and persist new seeds. Returns a :class:`SeedingOutcome`."""
        seed_repo = SeedRepository(session)
        dec_repo = SeedDecisionRepository(session)
        outcome = SeedingOutcome()

        candidates = self._gather(subject, entities, expansion, gap)
        since = datetime.now(UTC) - timedelta(hours=1)
        created_last_hour = seed_repo.count_recent_auto(run_id, since=since)

        for cand in candidates:
            if max_new is not None and outcome.total_created >= max_new:
                break

            # De-dup against the persisted store.
            if seed_repo.exists_identical(run_id, cand.subject_type.value, cand.identifiers):
                outcome.rejected_duplicate += 1
                continue

            # Safety budget (rate).
            rate = self.budget.check_rate(created_last_hour)
            if not rate.allowed:
                outcome.blocked += 1
                dec_repo.record(
                    run_id,
                    decision="blocked",
                    source=cand.source,
                    rationale=rate.reason,
                    rule=cand.rule,
                    payload={"identifiers": cand.identifiers, "type": cand.subject_type.value},
                )
                audit.emit(
                    "seed.blocked",
                    reason=rate.reason,
                    identifiers=cand.identifiers,
                    source=cand.source,
                )
                break  # rate cap hit — stop creating for this pass

            status = (
                SeedStatus.PENDING if self.budget.require_approval else SeedStatus.APPROVED
            )
            rec = seed_repo.add(
                run_id,
                subject_type=cand.subject_type.value,
                identifiers=cand.identifiers,
                context=cand.context,
                origin="auto",
                depth=depth + 1,
                parent_seed_id=parent_seed_id,
                status=status,
                score=cand.priority,
                tags=[f"source:{cand.source}"],
            )
            created_last_hour += 1
            outcome.created_seed_ids.append(rec.id)
            if status is SeedStatus.PENDING:
                outcome.pending += 1
            else:
                outcome.approved += 1

            dec_repo.record(
                run_id,
                decision=status.value,
                source=cand.source,
                rationale=cand.rationale,
                rule=cand.rule,
                seed_id=rec.id,
                payload={"identifiers": cand.identifiers, "priority": cand.priority},
            )
            audit.emit(
                "seed.proposed",
                seed_id=rec.id,
                status=status.value,
                source=cand.source,
                identifiers=cand.identifiers,
                priority=round(cand.priority, 3),
            )

        log.info(
            "seeding.done",
            run_id=run_id,
            created=outcome.total_created,
            pending=outcome.pending,
            approved=outcome.approved,
            blocked=outcome.blocked,
            duplicates=outcome.rejected_duplicate,
        )
        return outcome

    def approve(self, session: Session, audit: AuditLog, run_id: str, seed_id: str) -> bool:
        """Approve a pending seed (human-in-the-loop). Returns success."""
        seed_repo = SeedRepository(session)
        rec = seed_repo.get(seed_id)
        if rec is None or rec.status != SeedStatus.PENDING.value:
            return False
        seed_repo.set_status(seed_id, SeedStatus.APPROVED)
        SeedDecisionRepository(session).record(
            run_id, decision="approved", source="human", rationale="manual approval", seed_id=seed_id
        )
        audit.emit("seed.approved", seed_id=seed_id, actor="human")
        return True

    def reject(self, session: Session, audit: AuditLog, run_id: str, seed_id: str) -> bool:
        """Reject a pending seed. Returns success."""
        seed_repo = SeedRepository(session)
        rec = seed_repo.get(seed_id)
        if rec is None or rec.status != SeedStatus.PENDING.value:
            return False
        seed_repo.set_status(seed_id, SeedStatus.REJECTED)
        SeedDecisionRepository(session).record(
            run_id, decision="rejected", source="human", rationale="manual rejection", seed_id=seed_id
        )
        audit.emit("seed.rejected", seed_id=seed_id, actor="human")
        return True
