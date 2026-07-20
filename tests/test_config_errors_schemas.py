"""Config, error-taxonomy, and schema contract tests."""

from __future__ import annotations

import pytest
from aetherseed.config import Settings
from aetherseed.errors import (
    ErrorCategory,
    PolicyError,
    TransientError,
    classify_exception,
)
from aetherseed.schemas import InvestigationRun, Lead, SubjectSeed, SubjectType
from pydantic import ValidationError


# --- config ---
def test_egress_allowlist_parsing() -> None:
    s = Settings(acq_egress_allowlist="a.com, 10.0.0.0/8 , ")
    assert s.egress_allowlist_entries == ["a.com", "10.0.0.0/8"]


def test_cors_and_prod_flag() -> None:
    s = Settings(cors_origins="http://a, http://b", env="prod")
    assert s.cors_origin_list == ["http://a", "http://b"]
    assert s.is_prod is True


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="verbose")


# --- errors ---
def test_classify_exception() -> None:
    assert classify_exception(TransientError("x")) is ErrorCategory.TRANSIENT
    assert classify_exception(ValueError("x")) is ErrorCategory.PERMANENT


def test_policy_error_not_retryable_by_default() -> None:
    err = PolicyError("blocked")
    assert err.retryable is False
    assert err.to_dict()["category"] == "policy"


# --- schemas ---
def test_lead_score_is_composite() -> None:
    lead = Lead(title="x", relevance=1.0, confidence=1.0, novelty=1.0)
    assert lead.score == pytest.approx(1.0)


def test_subject_seed_strips_and_requires_identifiers() -> None:
    seed = SubjectSeed(subject_type=SubjectType.PERSON, primary_identifiers=["  Jane  ", ""])
    assert seed.primary_identifiers == ["Jane"]
    with pytest.raises(ValidationError):
        SubjectSeed(subject_type=SubjectType.PERSON, primary_identifiers=["  ", ""])


def test_top_leads_sorted() -> None:
    run = InvestigationRun(
        subject=SubjectSeed(subject_type=SubjectType.CUSTOM, primary_identifiers=["x"]),
        new_leads=[Lead(title="low", relevance=0.1), Lead(title="high", relevance=0.9)],
    )
    assert run.top_leads(1)[0].title == "high"
