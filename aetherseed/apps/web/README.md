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

A working app lives in [`frontend/`](frontend/) — Next.js 15 (App Router) +
React 19 + React Flow + TanStack Query + Tailwind.

```bash
cd apps/web/frontend
cp .env.local.example .env.local     # NEXT_PUBLIC_API_URL -> the API
npm install
npm run dev                          # http://localhost:3000
```

Start the API allowing that origin:

```bash
AETHERSEED_CORS_ORIGINS=http://localhost:3000 uv run aetherseed serve
```

It consumes `POST /v1/investigations`, polls `GET /v1/investigations/{id}`, and
renders `GET /v1/investigations/{id}/graph` + `/result`. See
[`frontend/README.md`](frontend/README.md).
