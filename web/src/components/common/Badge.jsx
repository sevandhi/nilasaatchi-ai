import { confidenceColor, verdictColor, fmtPct } from "../../lib/format.js";

export function VerdictBadge({ verdict }) {
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${verdictColor(verdict)}`}>
      {verdict || "UNVERIFIED"}
    </span>
  );
}

export function ConfidencePill({ value }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-gray-600" title="Model/verification confidence">
      <span className={`h-2 w-2 rounded-full ${confidenceColor(value)}`} />
      {fmtPct(value)}
    </span>
  );
}

export function StatusBadge({ status }) {
  const map = {
    verified: "bg-emerald-100 text-emerald-800",
    downgraded: "bg-amber-100 text-amber-800",
    review: "bg-gray-200 text-gray-700",
    open: "bg-gray-200 text-gray-700",
    auto: "bg-emerald-100 text-emerald-800",
    queued: "bg-gray-200 text-gray-700",
    auto_unverified: "bg-amber-100 text-amber-800",
  };
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${map[status] || "bg-gray-100 text-gray-600"}`}>{status || "—"}</span>;
}

export function PrivacyTierBadge({ tier }) {
  const isPii = tier === "PII";
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${isPii ? "bg-rose-100 text-rose-800" : "bg-sky-100 text-sky-800"}`}
      title={isPii ? "Contains personal data — never routed to a training-tier model" : "No personal data"}
    >
      {tier || "—"}
    </span>
  );
}
