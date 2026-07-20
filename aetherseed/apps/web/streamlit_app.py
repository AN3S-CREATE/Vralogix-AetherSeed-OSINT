"""Streamlit MVP UI for AetherSeed.

A thin operator console over the FastAPI service: launch an investigation, poll
its status, and inspect leads / gap report / graph stats.

Run::

    uv sync --extra web
    uv run aetherseed serve            # in one terminal (API)
    uv run streamlit run aetherseed/apps/web/streamlit_app.py   # in another
"""

from __future__ import annotations

import os
import time

import httpx
import streamlit as st

API = os.environ.get("AETHERSEED_API_URL", "http://localhost:8000")

st.set_page_config(page_title="AetherSeed OSINT", page_icon="🛰️", layout="wide")
st.title("🛰️ Veralogix AetherSeed OSINT")
st.caption("Local-first investigative research. Lawful, authorised use only.")

with st.sidebar:
    st.header("New investigation")
    subject_type = st.selectbox("Subject type", ["company", "person", "domain", "event", "custom"])
    identifiers = st.text_area("Identifiers (one per line)", "https://example.com/")
    context = st.text_area("Investigation brief", "")
    max_depth = st.slider("Max crawl depth", 0, 5, 2)
    auto_seed = st.checkbox("Auto-seed", value=False)
    enrich = st.checkbox("Enrichment pass", value=False)
    render = st.checkbox("JS render (Playwright)", value=False)
    launch = st.button("Start investigation", type="primary")

if launch:
    payload = {
        "subject": {
            "subject_type": subject_type,
            "primary_identifiers": [i.strip() for i in identifiers.splitlines() if i.strip()],
            "context": context,
            "constraints": {"max_depth": max_depth},
        },
        "auto_seed": auto_seed,
        "enrich": enrich,
        "render": render,
    }
    try:
        resp = httpx.post(f"{API}/v1/investigations", json=payload, timeout=30)
        resp.raise_for_status()
        st.session_state["run_id"] = resp.json()["run_id"]
    except httpx.HTTPError as exc:
        st.error(f"Failed to start: {exc}")

run_id = st.session_state.get("run_id")
if run_id:
    st.subheader(f"Run `{run_id}`")
    status_box = st.empty()
    for _ in range(120):
        try:
            status = httpx.get(f"{API}/v1/investigations/{run_id}", timeout=10).json()
        except httpx.HTTPError:
            break
        status_box.info(f"status: {status.get('status')}")
        if status.get("ready"):
            break
        time.sleep(1.0)

    try:
        result = httpx.get(f"{API}/v1/investigations/{run_id}/result", timeout=30).json()
    except httpx.HTTPError as exc:
        st.warning(f"Result not available: {exc}")
        result = None

    if result:
        m = result["metrics"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Pages", m["pages_fetched"])
        c2.metric("Entities", len(result["graph_delta"]["nodes"]))
        c3.metric("Leads", len(result["new_leads"]))
        c4.metric("Coverage", f"{result['gap_report']['coverage_score']:.2f}")

        st.subheader("Top leads")
        st.dataframe(
            [
                {"type": ld["lead_type"], "title": ld["title"], "risk": ld["risk"]}
                for ld in result["new_leads"][:25]
            ],
            use_container_width=True,
        )

        st.subheader("Gap report")
        st.json(result["gap_report"])
