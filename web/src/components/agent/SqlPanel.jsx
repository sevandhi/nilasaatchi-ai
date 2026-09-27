import { useState } from "react";

/** Syntax-highlighted-ish SQL/GIS panel with copy, per `spec.sql` (phase6 SKILL: SqlPanel). */
export function SqlPanel({ entries }) {
  if (!entries?.length) return <p className="text-xs text-gray-400">No SQL/GIS operations recorded for this run.</p>;
  return (
    <div className="space-y-2">
      {entries.map((e) => (
        <SqlBlock key={e.step_id} entry={e} />
      ))}
    </div>
  );
}

function SqlBlock({ entry }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded border border-gray-200 bg-gray-900 p-2 text-xs text-gray-100">
      <div className="mb-1 flex items-center justify-between text-gray-400">
        <span className="font-mono">{entry.step_id}</span>
        <button
          onClick={() => {
            navigator.clipboard?.writeText(entry.sql);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }}
          className="rounded bg-gray-700 px-2 py-0.5 text-[10px] hover:bg-gray-600"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="overflow-auto whitespace-pre-wrap">{entry.sql}</pre>
    </div>
  );
}
