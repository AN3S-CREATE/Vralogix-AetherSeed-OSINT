"""Investigation pipelines — end-to-end orchestration of the six pillars.

:class:`aetherseed.pipelines.investigation.InvestigationPipeline` wires
acquisition, AI, graph, seeding, enrichment, and storage into a single
resumable, fault-isolated, fully-audited run producing an
:class:`~aetherseed.schemas.InvestigationRun`.
"""

from __future__ import annotations

from aetherseed.pipelines.investigation import InvestigationPipeline

__all__ = ["InvestigationPipeline"]
