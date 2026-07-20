# AetherSeed Web (Next.js + React Flow)

Interactive investigation console: launch a run, watch it complete, and explore
the connections / follow-the-money graph.

- Next.js 15 (App Router) + React 19
- React Flow (`@xyflow/react`) for the graph
- TanStack Query for API state
- Tailwind CSS

## Run

```bash
cp .env.local.example .env.local     # point NEXT_PUBLIC_API_URL at the API
npm install
npm run dev                          # http://localhost:3000
```

The API must be running and allow this origin:

```bash
# from the repo root
AETHERSEED_CORS_ORIGINS=http://localhost:3000 uv run aetherseed serve
```

## Build / typecheck

```bash
npm run typecheck
npm run build
```

## What it shows

- **Left**: subject form (type, identifiers, depth, auto-seed / enrich / search).
- **Center**: the knowledge graph from `GET /v1/investigations/{id}/graph`,
  colour-coded by entity type; `paid` / `controls` edges are animated.
- **Right**: metrics, scored leads (risk-coloured), and open questions from the
  gap report.
