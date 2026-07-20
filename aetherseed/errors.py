"""Structured error taxonomy.

Every failure in the platform is classified into one of three categories so that
the orchestrator can decide, uniformly, whether to retry, skip, or halt:

* ``transient``  — network blip, 429, 5xx, browser crash. Safe to retry.
* ``permanent``  — 404, parse failure, malformed input. Retrying will not help.
* ``policy``     — robots.txt disallow, SSRF block, budget/approval gate, PII rule.
  A deliberate refusal, never retried and surfaced prominently.

The :class:`AetherError` hierarchy carries this category plus a ``retryable``
flag so per-item fault isolation is consistent everywhere.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Coarse classification driving retry/skip/halt decisions."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POLICY = "policy"


class AetherError(Exception):
    """Base class for all platform errors.

    Parameters
    ----------
    message:
        Human-readable description.
    category:
        One of :class:`ErrorCategory`.
    retryable:
        Whether an automatic retry could plausibly succeed. Defaults to ``True``
        for transient and ``False`` otherwise.
    context:
        Structured, JSON-serialisable context (url, seed_id, status_code, ...).
        Never place secrets here.
    """

    default_category: ErrorCategory = ErrorCategory.PERMANENT

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory | None = None,
        retryable: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category or self.default_category
        self.retryable = (
            retryable if retryable is not None else self.category is ErrorCategory.TRANSIENT
        )
        self.context: dict[str, Any] = context or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the structured-error shape used in API/CLI output."""
        return {
            "error": type(self).__name__,
            "message": self.message,
            "category": self.category.value,
            "retryable": self.retryable,
            "context": self.context,
        }


class TransientError(AetherError):
    """A temporary failure; retrying later may succeed."""

    default_category = ErrorCategory.TRANSIENT


class PermanentError(AetherError):
    """A definitive failure; retrying will not help."""

    default_category = ErrorCategory.PERMANENT


class PolicyError(AetherError):
    """A deliberate refusal by a safety/compliance control."""

    default_category = ErrorCategory.POLICY


# --- Concrete, frequently-raised specialisations -----------------------------


class FetchError(TransientError):
    """A network fetch failed (timeout, connection reset, 5xx, 429)."""


class ParseError(PermanentError):
    """Content could not be parsed into the expected structure."""


class SSRFError(PolicyError):
    """A request targeted a disallowed (private/loopback/blocked) address."""


class RobotsDisallowedError(PolicyError):
    """robots.txt disallows fetching the URL and the override is not set."""


class BudgetExceededError(PolicyError):
    """A safety budget (seeds/hour, USD spend, approval gate) was hit."""


class ApprovalRequiredError(PolicyError):
    """An action is blocked pending human-in-the-loop approval."""


class BackendUnavailableError(TransientError):
    """An optional backend (Ollama, Redis, Neo4j) is not reachable."""


class ConfigurationError(PermanentError):
    """Invalid or missing configuration for a requested operation."""


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Best-effort classification of an arbitrary exception.

    Used at the boundary where third-party libraries raise their own error
    types. Falls back to ``permanent`` (the conservative choice: do not retry
    something we do not understand).

    Examples
    --------
    >>> classify_exception(TransientError("blip")).value
    'transient'
    >>> classify_exception(ValueError("bad")).value
    'permanent'
    """
    if isinstance(exc, AetherError):
        return exc.category
    # httpx / urllib timeouts and connection errors are transient.
    name = type(exc).__name__.lower()
    if any(tok in name for tok in ("timeout", "connect", "readerror", "poolerror")):
        return ErrorCategory.TRANSIENT
    return ErrorCategory.PERMANENT
