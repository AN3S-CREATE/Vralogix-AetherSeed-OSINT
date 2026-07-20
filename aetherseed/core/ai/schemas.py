"""LLM-facing structured-output schemas.

These are intentionally *flat and model-friendly* (names, not ids) so a local
model can fill them reliably. The engine maps them onto the richer domain models
in :mod:`aetherseed.schemas` (assigning ids, provenance, confidence).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _LLMBase(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate extra keys from the model


class LLMEntity(_LLMBase):
    name: str = Field(description="Canonical display name of the entity")
    type: str = Field(
        description="One of: person, company, domain, asset, transaction, location, "
        "document, account, event, other"
    )
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class LLMRelation(_LLMBase):
    source: str = Field(description="Name of the source entity")
    target: str = Field(description="Name of the target entity")
    type: str = Field(
        description="One of: owns, director_of, employed_by, associated_with, "
        "located_at, paid, mentions, registered, controls, related_to"
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class LLMExtraction(_LLMBase):
    """Entities and relationships extracted from a document."""

    entities: list[LLMEntity] = Field(default_factory=list)
    relationships: list[LLMRelation] = Field(default_factory=list)


class LLMSeedCandidate(_LLMBase):
    subject_type: str = Field(description="person | company | domain | event | custom")
    identifiers: list[str] = Field(min_length=1)
    rationale: str = ""
    priority: float = Field(default=0.5, ge=0.0, le=1.0)


class LLMSeedExpansion(_LLMBase):
    """Prospective expansion of an investigation subject."""

    search_queries: list[str] = Field(default_factory=list)
    social_handles: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)
    money_hypotheses: list[str] = Field(
        default_factory=list, description="Follow-the-money hypotheses to test"
    )
    candidate_seeds: list[LLMSeedCandidate] = Field(default_factory=list)


class LLMAction(_LLMBase):
    action: str = Field(description="e.g. crawl, enrich_whois, search_registry, screenshot")
    target: str
    rationale: str = ""
    priority: float = Field(default=0.5, ge=0.0, le=1.0)


class LLMGapAnalysis(_LLMBase):
    """What the investigation still lacks."""

    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_dimensions: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    recommended_actions: list[LLMAction] = Field(default_factory=list)


class LLMLeadAssessment(_LLMBase):
    """Model's scoring of a candidate lead."""

    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    why_it_matters: str = ""
