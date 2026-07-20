"use client";

import { useState } from "react";
import type { StartRequest, SubjectType } from "@/lib/api";

const SUBJECT_TYPES: SubjectType[] = ["company", "person", "domain", "event", "custom"];

export function InvestigationForm({
  onStart,
  disabled,
}: {
  onStart: (body: StartRequest) => void;
  disabled: boolean;
}) {
  const [type, setType] = useState<SubjectType>("company");
  const [identifiers, setIdentifiers] = useState("https://example.com/");
  const [context, setContext] = useState("");
  const [maxDepth, setMaxDepth] = useState(2);
  const [autoSeed, setAutoSeed] = useState(false);
  const [enrich, setEnrich] = useState(false);
  const [search, setSearch] = useState(false);

  function submit() {
    onStart({
      subject: {
        subject_type: type,
        primary_identifiers: identifiers
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        context,
        constraints: { max_depth: maxDepth },
      },
      auto_seed: autoSeed,
      enrich,
      search,
    });
  }

  return (
    <div className="space-y-3">
      <label className="block text-xs uppercase text-slate-400">Subject type</label>
      <select
        className="w-full rounded bg-ink p-2"
        value={type}
        onChange={(e) => setType(e.target.value as SubjectType)}
      >
        {SUBJECT_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <label className="block text-xs uppercase text-slate-400">Identifiers (one per line)</label>
      <textarea
        className="h-20 w-full rounded bg-ink p-2 font-mono text-sm"
        value={identifiers}
        onChange={(e) => setIdentifiers(e.target.value)}
      />

      <label className="block text-xs uppercase text-slate-400">Brief</label>
      <input
        className="w-full rounded bg-ink p-2"
        value={context}
        onChange={(e) => setContext(e.target.value)}
        placeholder="Ownership and connections…"
      />

      <label className="block text-xs uppercase text-slate-400">Max depth: {maxDepth}</label>
      <input
        type="range"
        min={0}
        max={5}
        value={maxDepth}
        onChange={(e) => setMaxDepth(Number(e.target.value))}
        className="w-full"
      />

      <div className="flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={autoSeed} onChange={(e) => setAutoSeed(e.target.checked)} />
          auto-seed
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} />
          enrich
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} />
          search
        </label>
      </div>

      <button
        onClick={submit}
        disabled={disabled}
        className="w-full rounded bg-emerald-500 py-2 font-semibold text-ink disabled:opacity-50"
      >
        {disabled ? "Running…" : "Start investigation"}
      </button>
    </div>
  );
}
