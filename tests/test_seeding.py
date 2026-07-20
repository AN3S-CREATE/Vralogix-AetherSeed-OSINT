"""Seeding-engine tests: gating, approval, dedup, audit (with DB)."""

from __future__ import annotations

from aetherseed.core.seeding.budget import SafetyBudget
from aetherseed.core.seeding.engine import SeedingEngine
from aetherseed.core.storage.audit import AuditLog
from aetherseed.core.storage.db import session_scope
from aetherseed.core.storage.repositories import (
    RunRepository,
    SeedDecisionRepository,
    SeedRepository,
)
from aetherseed.schemas import (
    Entity,
    EntityType,
    InvestigationRun,
    SeedStatus,
    SubjectSeed,
    SubjectType,
)


def _setup_run(env):
    subj = SubjectSeed(subject_type=SubjectType.COMPANY, primary_identifiers=["Root Co"])
    run = InvestigationRun(subject=subj)
    with session_scope() as s:
        RunRepository(s).create(run)
    entities = [
        Entity(type=EntityType.COMPANY, label="Alpha Ltd", confidence=0.8),
        Entity(type=EntityType.DOMAIN, label="alpha.co.za", confidence=0.7),
    ]
    return subj, run, entities


def test_require_approval_produces_pending(env) -> None:
    subj, run, entities = _setup_run(env)
    audit = AuditLog(run.run_id, env)
    with session_scope() as s:
        eng = SeedingEngine(SafetyBudget(env, require_approval=True))
        outcome = eng.propose(run.run_id, subj, entities, s, audit, max_new=10)
        decs = SeedDecisionRepository(s).list_by_run(run.run_id)
    assert outcome.total_created >= 2
    assert outcome.pending == outcome.total_created
    assert decs  # every decision recorded


def test_no_approval_produces_approved_and_dedups(env) -> None:
    subj, run, entities = _setup_run(env)
    audit = AuditLog(run.run_id, env)
    with session_scope() as s:
        eng = SeedingEngine(SafetyBudget(env, require_approval=False))
        first = eng.propose(run.run_id, subj, entities, s, audit, max_new=10)
        second = eng.propose(run.run_id, subj, entities, s, audit, max_new=10)
    assert first.approved >= 2
    assert second.rejected_duplicate >= 2  # nothing new the second time
    assert second.total_created == 0


def test_rate_cap_blocks(env) -> None:
    subj, run, entities = _setup_run(env)
    audit = AuditLog(run.run_id, env)
    with session_scope() as s:
        eng = SeedingEngine(SafetyBudget(env, require_approval=False, max_new_per_hour=1))
        outcome = eng.propose(run.run_id, subj, entities, s, audit, max_new=10)
    assert outcome.total_created == 1
    assert outcome.blocked >= 1


def test_human_approval_flow(env) -> None:
    subj, run, entities = _setup_run(env)
    audit = AuditLog(run.run_id, env)
    with session_scope() as s:
        eng = SeedingEngine(SafetyBudget(env, require_approval=True))
        outcome = eng.propose(run.run_id, subj, entities, s, audit, max_new=10)
        seed_id = outcome.created_seed_ids[0]
        assert eng.approve(s, audit, run.run_id, seed_id) is True
        rec = SeedRepository(s).get(seed_id)
    assert rec is not None and rec.status == SeedStatus.APPROVED.value
