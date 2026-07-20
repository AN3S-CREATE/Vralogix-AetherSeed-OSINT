"""Safety budgets for unattended seeding.

Bounds how fast and how expensively the auto-seeder may act:

* ``max_new_per_hour`` — rate cap on newly generated seeds.
* ``budget_usd`` — hard spend cap (0 => local-only, no paid calls permitted).
* ``require_approval`` — whether new seeds land as ``pending`` (awaiting a human)
  or ``approved`` (eligible to run immediately).

The engine consults a :class:`SafetyBudget` before creating any seed and records
a blocked decision in the audit trail when a limit is hit.
"""

from __future__ import annotations

from dataclasses import dataclass

from aetherseed.config import Settings, get_settings


@dataclass(slots=True)
class BudgetDecision:
    allowed: bool
    reason: str = ""


class SafetyBudget:
    """Encapsulates seeding rate/cost/approval policy for a run."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        max_new_per_hour: int | None = None,
        budget_usd: float | None = None,
        require_approval: bool | None = None,
    ) -> None:
        s = settings or get_settings()
        self.max_new_per_hour = (
            s.seed_max_new_per_hour if max_new_per_hour is None else max_new_per_hour
        )
        self.budget_usd = s.seed_budget_usd if budget_usd is None else budget_usd
        self.require_approval = (
            s.seed_require_approval if require_approval is None else require_approval
        )
        self._spent_usd = 0.0

    def check_rate(self, created_last_hour: int) -> BudgetDecision:
        """Whether another seed may be created given the hourly count so far."""
        if self.max_new_per_hour and created_last_hour >= self.max_new_per_hour:
            return BudgetDecision(
                False, f"hourly seed cap reached ({created_last_hour}/{self.max_new_per_hour})"
            )
        return BudgetDecision(True)

    def check_spend(self, cost_usd: float) -> BudgetDecision:
        """Whether a paid action of ``cost_usd`` is within the remaining budget."""
        if self._spent_usd + cost_usd > self.budget_usd:
            return BudgetDecision(
                False,
                f"spend cap reached (${self._spent_usd:.2f}+${cost_usd:.2f} > ${self.budget_usd:.2f})",
            )
        return BudgetDecision(True)

    def record_spend(self, cost_usd: float) -> None:
        self._spent_usd += cost_usd

    @property
    def spent_usd(self) -> float:
        return self._spent_usd
