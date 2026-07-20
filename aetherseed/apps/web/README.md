# Web UI

Two supported options.

## 1. Streamlit MVP (in-repo, fastest)

A minimal operator console that drives the API.

```bash
uv sync --extra web
uv run aetherseed serve                                   # API on :8000
uv run streamlit run aetherseed/apps/web/streamlit_app.py # UI on :8501
```

Set `AETHERSEED_API_URL` if the API is not on `http://localhost:8000`.

## 2. Next.js 15 + React Flow (production)

Recommended for graph-heavy interactive investigation. Suggested stack:

- Next.js 15 + React 19 + Tailwind + shadcn/ui
- **React Flow** for the connections / follow-the-money graph
- TanStack Query against the AetherSeed API (`/v1/investigations`, `/graph`)

Scaffold it under `apps/web/` (a separate Node project) and consume:

- `POST /v1/investigations` to launch,
- `GET /v1/investigations/{id}` to poll,
- `GET /v1/investigations/{id}/graph?fmt=cytoscape` to render the graph,
- `GET /v1/investigations/{id}/result` for leads + gap report.

CORS origins are controlled by `AETHERSEED_CORS_ORIGINS`.
