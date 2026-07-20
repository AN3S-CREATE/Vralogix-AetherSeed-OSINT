"use client";

import type { Lead } from "@/lib/api";

function riskColor(risk: number): string {
  if (risk >= 0.66) return "text-rose-400";
  if (risk >= 0.33) return "text-amber-400";
  return "text-slate-400";
}

export function LeadsTable({ leads }: { leads: Lead[] }) {
  if (leads.length === 0) {
    return <p className="text-sm text-slate-400">No leads yet.</p>;
  }
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-slate-400">
        <tr>
          <th className="py-1 pr-2">Type</th>
          <th className="py-1 pr-2">Title</th>
          <th className="py-1 pr-2">Risk</th>
        </tr>
      </thead>
      <tbody>
        {leads.slice(0, 30).map((lead) => (
          <tr key={lead.id} className="border-t border-slate-800 align-top">
            <td className="py-1 pr-2 text-slate-300">{lead.lead_type}</td>
            <td className="py-1 pr-2">
              <div className="text-slate-100">{lead.title}</div>
              {lead.why_it_matters && (
                <div className="text-xs text-slate-500">{lead.why_it_matters}</div>
              )}
            </td>
            <td className={`py-1 pr-2 ${riskColor(lead.risk)}`}>{lead.risk.toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
