import { useApi } from "../../hooks/useApi.js";
import { fmtDate, fmtUsd } from "../../lib/format.js";

export function StatusFooter() {
  const health = useApi("/health");
  const stats = useApi("/stats/overview");
  const now = new Date();
  const apiOk = health.status === "ok";

  const kpis = stats.data?.kpis || [];
  const ce = kpis.find((k) => k.id === "aws_spend_usd");
  const est = kpis.find((k) => k.id === "aws_spend_router_estimate_usd");
  const cap = 100; // FarmwiseAI event budget per team account (AWS access guide)

  return (
    <footer className="flex flex-wrap items-center gap-4 border-t border-gray-200 bg-white px-4 py-1.5 text-xs text-gray-500">
      <span className="flex items-center gap-1">
        <span className={`h-2 w-2 rounded-full ${apiOk ? "bg-emerald-500" : health.status === "loading" ? "bg-gray-300" : "bg-rose-500"}`} />
        API: {apiOk ? `ok (${health.data?.api_mode})` : health.status}
      </span>
      <span>DB: {apiOk ? "reachable via API" : "unknown"}</span>
      {ce || est ? (
        <span title="Cost Explorer lags ~1 day behind actual spend; the live estimate covers billed Bedrock calls.">
          AWS spend vs US${cap} event budget:{" "}
          {ce && <>Cost Explorer (lags ~1 day) {fmtUsd(ce.value, 2)}</>}
          {ce && est && " · "}
          {est && <>router estimate (live) {fmtUsd(est.value, 2)}</>}
        </span>
      ) : (
        <span>AWS spend vs US${cap} event budget: not available</span>
      )}
      <span className="ml-auto">Last refresh: {fmtDate(now)} {now.toLocaleTimeString()}</span>
    </footer>
  );
}
