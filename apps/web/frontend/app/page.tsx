"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { GraphView } from "@/components/GraphView";
import { InvestigationForm } from "@/components/InvestigationForm";
import { LeadsTable } from "@/components/LeadsTable";
import {
  getGraph,
  getResult,
  getStatus,
  startInvestigation,
  type StartRequest,
} from "@/lib/api";

export default function Home() {
  const [runId, setRunId] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: (body: StartRequest) => startInvestigation(body),
    onSuccess: (data) => setRunId(data.run_id),
  });

  const status = useQuery({
    queryKey: ["status", runId],
    queryFn: () => getStatus(runId as string),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.ready ? false : 1000),
  });

  const ready = !!status.data?.ready;

  const result = useQuery({
    queryKey: ["result", runId],
    queryFn: () => getResult(runId as string),
    enabled: !!runId && ready,
  });

  const graph = useQuery({
    queryKey: ["graph", runId],
    queryFn: () => getGraph(runId as string),
    enabled: !!runId && ready,
  });

  const running = start.isPending || (!!runId && !ready);

  return (
    <main className="grid h-screen grid-cols-[320px_1fr_360px] gap-px bg-slate-800">
      <aside className="overflow-y-auto bg-ink p-4">
        <h1 className="mb-3 text-lg font-bold">🛰️ AetherSeed</h1>
        <p className="mb-4 text-xs text-slate-500">
          Lawful, authorised investigative research only.
        </p>
        <InvestigationForm onStart={(b) => start.mutate(b)} disabled={running} />
        {runId && (
          <p className="mt-4 text-xs text-slate-400">
            run <span className="font-mono">{runId}</span> — {status.data?.status ?? "…"}
          </p>
        )}
        {start.isError && (
          <p className="mt-2 text-xs text-rose-400">
            {(start.error as Error).message} — is the API running?
          </p>
        )}
      </aside>

      <section className="relative bg-ink">
        <GraphView graph={graph.data} />
      </section>

      <aside className="overflow-y-auto bg-ink p-4">
        {result.data ? (
          <>
            <div className="mb-4 grid grid-cols-2 gap-2 text-center">
              <Stat label="Pages" value={result.data.metrics.pages_fetched} />
              <Stat label="Entities" value={result.data.graph_delta.nodes.length} />
              <Stat label="Leads" value={result.data.new_leads.length} />
              <Stat
                label="Coverage"
                value={result.data.gap_report.coverage_score.toFixed(2)}
              />
            </div>
            <h2 className="mb-2 text-sm font-semibold text-slate-300">Leads</h2>
            <LeadsTable leads={result.data.new_leads} />
            <h2 className="mb-2 mt-4 text-sm font-semibold text-slate-300">Gaps</h2>
            <ul className="list-inside list-disc text-xs text-slate-400">
              {result.data.gap_report.unanswered_questions.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-slate-400">
            {running ? "Investigation running…" : "Results will appear here."}
          </p>
        )}
      </aside>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded bg-panel p-2">
      <div className="text-lg font-bold text-emerald-400">{value}</div>
      <div className="text-xs text-slate-400">{label}</div>
    </div>
  );
}
