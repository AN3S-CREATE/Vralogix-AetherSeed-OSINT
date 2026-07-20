// Typed client for the AetherSeed FastAPI service.

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type SubjectType = "person" | "company" | "domain" | "event" | "custom";

export interface StartRequest {
  subject: {
    subject_type: SubjectType;
    primary_identifiers: string[];
    context?: string;
    constraints?: { max_depth?: number };
  };
  auto_seed?: boolean;
  enrich?: boolean;
  render?: boolean;
  search?: boolean;
}

export interface Lead {
  id: string;
  title: string;
  lead_type: string;
  value: string;
  why_it_matters: string;
  relevance: number;
  risk: number;
  confidence: number;
}

export interface GraphDeltaNode {
  id: string;
  type: string;
  label: string;
}

export interface InvestigationResult {
  run_id: string;
  status: string;
  metrics: {
    pages_fetched: number;
    seeds_generated: number;
    failed: number;
  };
  new_leads: Lead[];
  graph_delta: { nodes: GraphDeltaNode[]; edges: unknown[] };
  gap_report: {
    coverage_score: number;
    missing_dimensions: string[];
    unanswered_questions: string[];
  };
}

// networkx node-link export shape.
export interface NodeLinkGraph {
  nodes: Array<{ id: string; type?: string; label?: string }>;
  links: Array<{ source: string; target: string; key?: string; type?: string }>;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function startInvestigation(body: StartRequest): Promise<{ run_id: string }> {
  const res = await fetch(`${API_URL}/v1/investigations`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return json(res);
}

export async function getStatus(runId: string): Promise<{ status: string; ready: boolean }> {
  return json(await fetch(`${API_URL}/v1/investigations/${runId}`));
}

export async function getResult(runId: string): Promise<InvestigationResult> {
  return json(await fetch(`${API_URL}/v1/investigations/${runId}/result`));
}

export async function getGraph(runId: string): Promise<NodeLinkGraph> {
  return json(await fetch(`${API_URL}/v1/investigations/${runId}/graph?fmt=node-link`));
}
