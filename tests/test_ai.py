"""AetherMind engine + backend tests (deterministic, no model)."""

from __future__ import annotations

import pytest
from aetherseed.core.ai.backend import (
    NullBackend,
    _extract_json,
    _StructuredMixin,
    get_llm_backend,
)
from aetherseed.core.ai.engine import AetherMind
from aetherseed.core.interfaces import ExtractedContent
from aetherseed.errors import BackendUnavailableError
from aetherseed.schemas import Constraints, SubjectSeed, SubjectType


def test_extract_json_from_noisy_output() -> None:
    raw = 'Sure! Here you go:\n```json\n{"a": 1, "b": {"c": 2}}\n```\nThanks'
    assert _extract_json(raw) == {"a": 1, "b": {"c": 2}}


class _CannedBackend(_StructuredMixin):
    """A structured backend whose completions are pre-scripted (for the repair loop)."""

    name = "canned"
    model = "canned"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)

    def available(self) -> bool:
        return True

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        return self._replies.pop(0)


async def test_structured_repair_loop() -> None:
    from aetherseed.core.ai.schemas import LLMGapAnalysis

    backend = _CannedBackend(["this is not json at all", '{"coverage_score": 0.5}'])
    out = await backend.structured("prompt", LLMGapAnalysis)
    assert out.coverage_score == 0.5


async def test_null_backend_raises() -> None:
    backend = NullBackend()
    assert backend.available() is True
    with pytest.raises(BackendUnavailableError):
        await backend.complete("hi")


def test_factory_returns_null_when_configured(env) -> None:
    assert get_llm_backend(env).name == "null"


async def test_heuristic_seed_expansion() -> None:
    mind = AetherMind(backend=NullBackend())
    assert mind.uses_model() is False
    subj = SubjectSeed(subject_type=SubjectType.COMPANY, primary_identifiers=["Acme Mining Pty Ltd"])
    exp = await mind.expand_seed(subj)
    assert exp.search_queries
    assert any("director" in q.lower() for q in exp.search_queries)
    assert exp.money_hypotheses  # companies get money hypotheses


async def test_heuristic_extract_uses_regex() -> None:
    mind = AetherMind(backend=NullBackend())
    content = ExtractedContent(url="http://x", text="Email a@b.co.za about Beta Mining Pty Ltd")
    entities, rels = await mind.extract(content)
    assert entities
    assert rels == []  # no relationships without a model


async def test_heuristic_gap_analysis() -> None:
    mind = AetherMind(backend=NullBackend())
    subj = SubjectSeed(
        subject_type=SubjectType.COMPANY,
        primary_identifiers=["Acme"],
        constraints=Constraints(),
    )
    gap = await mind.analyze_gaps(subj, [])
    assert 0.0 <= gap.coverage_score <= 1.0
    assert gap.missing_dimensions
