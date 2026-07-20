"""FastAPI service for AetherSeed.

Endpoints (all under ``/v1`` except health/metrics):

* ``POST /v1/investigations``            — start a run (returns run_id immediately)
* ``GET  /v1/investigations``            — list recent runs
* ``GET  /v1/investigations/{id}``       — poll status
* ``GET  /v1/investigations/{id}/result``— full structured result
* ``GET  /v1/investigations/{id}/graph`` — export graph (fmt query param)
* ``GET  /v1/investigations/{id}/seeds`` — list seeds
* ``POST /v1/investigations/{id}/seeds/{seed_id}/{approve|reject}``
* ``GET  /health`` and ``GET /metrics``

CORS is restricted to the configured origins; nothing is exposed by default
beyond localhost dev origins.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, Field

from aetherseed import __version__
from aetherseed.apps.api.service import get_registry
from aetherseed.config import get_settings
from aetherseed.core.graph.store import NetworkXGraphStore
from aetherseed.core.storage.audit import AuditLog
from aetherseed.core.storage.db import init_db, session_scope
from aetherseed.core.storage.repositories import RunRepository, SeedRepository
from aetherseed.logging import configure_logging
from aetherseed.schemas import SubjectSeed

_RUNS_STARTED = Counter("aetherseed_runs_started_total", "Investigations started")

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.log_json)

app = FastAPI(
    title="Veralogix AetherSeed OSINT",
    version=__version__,
    description="Local-first investigative research platform API.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class InvestigationRequest(BaseModel):
    subject: SubjectSeed
    auto_seed: bool = False
    screenshots: bool = False
    enrich: bool = False
    render: bool = False


class InvestigationAccepted(BaseModel):
    run_id: str
    status: str = "running"
    poll: str = Field(description="URL to poll for status")


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    init_db(settings)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "env": settings.env}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/investigations", response_model=InvestigationAccepted, status_code=202)
async def start_investigation(req: InvestigationRequest) -> InvestigationAccepted:
    """Start an investigation; returns a run_id to poll. Non-blocking."""
    _RUNS_STARTED.inc()
    run_id = get_registry().start(
        req.subject,
        auto_seed=req.auto_seed,
        screenshots=req.screenshots,
        enrich=req.enrich,
        render=req.render,
    )
    return InvestigationAccepted(run_id=run_id, poll=f"/v1/investigations/{run_id}")


@app.get("/v1/investigations")
def list_investigations(limit: int = 20) -> dict[str, Any]:
    with session_scope() as session:
        rows = RunRepository(session).list_recent(limit)
        return {
            "runs": [
                {"run_id": r.run_id, "status": r.status, "created_at": str(r.created_at)}
                for r in rows
            ]
        }


@app.get("/v1/investigations/{run_id}")
def get_status(run_id: str) -> dict[str, Any]:
    payload = get_registry().as_status_payload(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="run not found")
    return payload


@app.get("/v1/investigations/{run_id}/result")
def get_result(run_id: str) -> dict[str, Any]:
    result = get_registry().result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="result not available yet")
    return result.model_dump(mode="json")


@app.get("/v1/investigations/{run_id}/graph")
def get_graph(run_id: str, fmt: str = "node-link") -> Any:
    result = get_registry().result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="result not available yet")
    store = NetworkXGraphStore(graph_id=result.subject.existing_graph_id)
    store.apply_delta(result.graph_delta)
    try:
        exported = store.export(fmt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exported, str):
        return Response(exported, media_type="application/xml")
    return exported


@app.get("/v1/investigations/{run_id}/seeds")
def list_seeds(run_id: str) -> dict[str, Any]:
    with session_scope() as session:
        rows = SeedRepository(session).list_by_run(run_id)
        return {
            "seeds": [
                {
                    "id": s.id,
                    "subject_type": s.subject_type,
                    "identifiers": s.identifiers,
                    "status": s.status,
                    "origin": s.origin,
                    "score": s.score,
                }
                for s in rows
            ]
        }


@app.post("/v1/investigations/{run_id}/seeds/{seed_id}/{action}")
def seed_action(run_id: str, seed_id: str, action: str) -> dict[str, Any]:
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    from aetherseed.core.seeding.engine import SeedingEngine

    audit = AuditLog(run_id)
    with session_scope() as session:
        engine = SeedingEngine()
        ok = (
            engine.approve(session, audit, run_id, seed_id)
            if action == "approve"
            else engine.reject(session, audit, run_id, seed_id)
        )
    if not ok:
        raise HTTPException(status_code=404, detail="seed not found or not actionable")
    return {"run_id": run_id, "seed_id": seed_id, "action": action, "ok": True}
