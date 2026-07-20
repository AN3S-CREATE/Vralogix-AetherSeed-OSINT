"""FastAPI smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_metrics(env) -> None:
    from aetherseed.apps.api.main import app

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/metrics").status_code == 200


def test_unknown_run_is_404(env) -> None:
    from aetherseed.apps.api.main import app

    with TestClient(app) as client:
        assert client.get("/v1/investigations/run_missing").status_code == 404


def test_start_investigation_accepted(env) -> None:
    from aetherseed.apps.api.main import app

    payload = {
        "subject": {
            "subject_type": "company",
            "primary_identifiers": ["Some Company"],  # non-crawlable => fast
        },
        "auto_seed": False,
    }
    with TestClient(app) as client:
        resp = client.post("/v1/investigations", json=payload)
        assert resp.status_code == 202
        body = resp.json()
        assert body["run_id"].startswith("run_")
        assert body["poll"].endswith(body["run_id"])
