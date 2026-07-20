"""Automated seeding: logic rules + gap-driven expansion under safety budgets.

The seeding engine turns discovered entities and gap analyses into new seeds,
combining three sources:

* **Rules** — data-driven (YAML/JSON + defaults) with optional Python hooks.
* **LLM proposals** — candidate seeds from :class:`~aetherseed.core.ai.engine.
  AetherMind`.
* **Gap actions** — crawl/enrich actions surfaced by gap analysis.

Every proposal passes through de-duplication, a safety budget (max new seeds /
hour, spend cap), and a human-in-the-loop approval gate. Every decision — accept,
reject, or block — is written to the audit trail.
"""

from __future__ import annotations
