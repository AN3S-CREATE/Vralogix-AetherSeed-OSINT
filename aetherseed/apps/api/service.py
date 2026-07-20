"""API service layer: run registry + durable result persistence.

Runs execute as background asyncio tasks. Their live status is tracked in an
in-process registry; final results are also written to
``<data_dir>/runs/<run_id>.json`` so they survive restarts and can be reloaded
on demand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aetherseed.config import get_settings
from aetherseed.logging import get_logger
from aetherseed.pipelines import InvestigationPipeline
from aetherseed.schemas import InvestigationRun, RunStatus, SubjectSeed

log = get_logger(__name__)


class RunRegistry:
    """Tracks in-flight and completed runs."""

    def __init__(self) -> None:
        self._status: dict[str, str] = {}
        self._results: dict[str, InvestigationRun] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._runs_dir = get_settings().data_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self._runs_dir / f"{run_id}.json"

    def start(
        self,
        subject: SubjectSeed,
        *,
        auto_seed: bool = False,
        screenshots: bool = False,
        enrich: bool = False,
        render: bool = False,
        search: bool = False,
    ) -> str:
        """Launch an investigation in the background; return its run_id."""
        result = InvestigationRun(subject=subject)
        run_id = result.run_id
        self._status[run_id] = RunStatus.RUNNING.value

        async def _run() -> None:
            try:
                pipeline = InvestigationPipeline()
                res = await pipeline.run(
                    subject,
                    run_id=run_id,
                    auto_seed=auto_seed,
                    take_screenshots=screenshots,
                    enrich=enrich,
                    render=render,
                    search=search,
                )
                self._results[run_id] = res
                self._status[run_id] = res.status.value
                self._persist(res)
            except Exception as exc:
                log.error("api.run_failed", run_id=run_id, error=str(exc))
                self._status[run_id] = RunStatus.FAILED.value

        self._tasks[run_id] = asyncio.create_task(_run())
        return run_id

    def _persist(self, result: InvestigationRun) -> None:
        self._path(result.run_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")

    def status(self, run_id: str) -> str | None:
        return self._status.get(run_id)

    def result(self, run_id: str) -> InvestigationRun | None:
        if run_id in self._results:
            return self._results[run_id]
        path = self._path(run_id)
        if path.exists():
            res = InvestigationRun.model_validate_json(path.read_text(encoding="utf-8"))
            self._results[run_id] = res
            return res
        return None

    def as_status_payload(self, run_id: str) -> dict[str, Any] | None:
        status = self.status(run_id)
        if status is None and not self._path(run_id).exists():
            return None
        result = self.result(run_id)
        return {
            "run_id": run_id,
            "status": status or (result.status.value if result else "unknown"),
            "ready": run_id not in self._tasks or self._tasks[run_id].done(),
        }


_registry: RunRegistry | None = None


def get_registry() -> RunRegistry:
    global _registry
    if _registry is None:
        _registry = RunRegistry()
    return _registry
