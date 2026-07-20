"""Celery tasks (optional).

Guarded so the module imports even without Celery installed. When the ``queue``
extra is present and ``AETHERSEED_REDIS_URL`` is set, ``celery_app`` is a real
Celery application and :func:`run_investigation_task` executes a full pipeline
run on a worker.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aetherseed.config import get_settings
from aetherseed.logging import get_logger
from aetherseed.pipelines import InvestigationPipeline
from aetherseed.schemas import SubjectSeed

log = get_logger(__name__)


def _make_celery() -> Any | None:
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        from celery import Celery
    except ImportError:
        log.info("worker.celery_not_installed")
        return None
    app = Celery("aetherseed", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.task_acks_late = True
    app.conf.worker_prefetch_multiplier = 1
    return app


celery_app = _make_celery()


def run_investigation_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Run an investigation synchronously from a serialised request payload."""
    subject = SubjectSeed.model_validate(payload["subject"])
    result = asyncio.run(
        InvestigationPipeline().run(
            subject,
            auto_seed=payload.get("auto_seed", False),
            take_screenshots=payload.get("screenshots", False),
            enrich=payload.get("enrich", False),
            render=payload.get("render", False),
        )
    )
    return result.model_dump(mode="json")


if celery_app is not None:  # pragma: no cover - requires broker

    @celery_app.task(name="aetherseed.run_investigation")  # type: ignore[untyped-decorator]
    def run_investigation_task(payload: dict[str, Any]) -> dict[str, Any]:
        return run_investigation_sync(payload)
