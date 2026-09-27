import { RouterModelsResponseSchema, RouterChainsResponseSchema, RouterUsageResponseSchema, StatsOverviewResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { fmtPct, fmtUsd } from "../lib/format.js";

/** Page 7: model registry, task→chain table, router usage, PII-tier audit, AWS spend vs the US$100 event budget. */
export function ModelsRouting() {
  const models = useApi("/router/models", { schema: RouterModelsResponseSchema });
  const chains = useApi("/router/chains", { schema: RouterChainsResponseSchema });
  const usage = useApi("/router/usage", { schema: RouterUsageResponseSchema });
  const stats = useApi("/stats/overview", { schema: StatsOverviewResponseSchema });
  const awsKpi = stats.data?.kpis.find((k) => k.id === "aws_spend_usd");
  const awsEstKpi = stats.data?.kpis.find((k) => k.id === "aws_spend_router_estimate_usd");

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Models &amp; routing</h1>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">AWS spend vs US$100 event budget</h2>
        <Widget status={stats.status} error={stats.error} onRetry={stats.reload} title="AWS spend" minHeight="3rem">
          {awsKpi && (
            <p className="text-sm">
              Cost Explorer (lags ~1 day): <b>{awsKpi.value !== null ? fmtUsd(awsKpi.value, 2) : "not run yet"}</b> of the US$100 event budget
              {awsEstKpi && <> · router estimate (live, Bedrock calls only): <b>{fmtUsd(awsEstKpi.value, 2)}</b></>}
              {awsKpi.breakdown?.fetched_at ? ` (Cost Explorer fetched ${new Date(awsKpi.breakdown.fetched_at * 1000).toLocaleString()})` : ""}.
            </p>
          )}
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Model registry</h2>
        <Widget status={models.status} error={models.error} onRetry={models.reload} title="Registry" minHeight="8rem">
          {models.data && (
            <div className="overflow-auto rounded border border-gray-200">
              <table className="min-w-full text-xs">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-2 py-1 text-left">id</th>
                    <th className="px-2 py-1 text-left">vendor</th>
                    <th className="px-2 py-1 text-left">weights</th>
                    <th className="px-2 py-1 text-left">modalities</th>
                    <th className="px-2 py-1 text-left">trains on free tier?</th>
                    <th className="px-2 py-1 text-left">cost class</th>
                    <th className="px-2 py-1 text-left">health check: latency / vision</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {models.data.models.map((m) => (
                    <tr key={m.id}>
                      <td className="px-2 py-1 font-mono">{m.id}</td>
                      <td className="px-2 py-1">{m.vendor}</td>
                      <td className="px-2 py-1">{m.weights}</td>
                      <td className="px-2 py-1">{(m.modalities || []).join(", ")}</td>
                      <td className={`px-2 py-1 font-medium ${m.trains_on_free_tier ? "text-rose-600" : "text-emerald-600"}`}>{m.trains_on_free_tier ? "yes" : "no"}</td>
                      <td className="px-2 py-1">{m.cost_class}</td>
                      <td className="px-2 py-1">{m.doctor?.latency_ms ?? m.doctor?.p50_ms ?? "—"} ms{m.doctor?.vision_proof !== undefined ? ` · vision: ${m.doctor.vision_proof ? "ok" : "no"}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 px-2 text-[11px] text-gray-400">last health check: {models.data.doctor?.generated_at || "—"}</p>
            </div>
          )}
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Task → chain</h2>
        <Widget status={chains.status} error={chains.error} onRetry={chains.reload} title="Chains" minHeight="6rem">
          {chains.data && (
            <ul className="space-y-1 text-xs">
              {chains.data.tasks.map((t) => (
                <li key={t.task} className="rounded border border-gray-200 p-2">
                  <b>{t.task}</b> ({t.typical_tier}) — {(t.chain || []).map((c) => `${c.model} (cap ${c.capability})`).join(" → ")}
                  {t.terminal ? <span className="ml-2 text-gray-400">terminal: {t.terminal}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Usage (router_log)</h2>
        <Widget status={usage.status} error={usage.error} onRetry={usage.reload} title="Usage" minHeight="8rem" emptyReason="No router_log.sqlite rows yet.">
          {usage.data && (
            <div className="space-y-3">
              <DataTable
                columns={[
                  { key: "model_id", label: "Model" },
                  { key: "task", label: "Task" },
                  { key: "calls", label: "Calls", type: "number" },
                  { key: "success_rate", label: "Success rate", type: "number" },
                  { key: "fallback_rate", label: "Fallback rate", type: "number" },
                  { key: "shadow_cost_usd", label: "Shadow $", type: "number" },
                  { key: "actual_cost_usd", label: "Actual $", type: "number" },
                ]}
                rows={usage.data.by_model_task.map((r) => ({ ...r, success_rate: fmtPct(r.success_rate), fallback_rate: fmtPct(r.fallback_rate), shadow_cost_usd: fmtUsd(r.shadow_cost_usd), actual_cost_usd: fmtUsd(r.actual_cost_usd) }))}
                rowKey={(r) => `${r.model_id}-${r.task}`}
                csvName="router-usage"
              />
              <div className="rounded border border-gray-200 p-2 text-xs">
                <b>Latency p50/p95 by model/task:</b>
                <ul className="mt-1 grid grid-cols-2 gap-x-4 sm:grid-cols-3">
                  {usage.data.latency.map((l, i) => (
                    <li key={i}>{l.model_id} / {l.task}: {l.p50_ms}ms / {l.p95_ms}ms (n={l.n})</li>
                  ))}
                </ul>
              </div>
              <div className={`rounded border p-2 text-xs ${usage.data.pii_audit.pii_to_training_tier > 0 ? "border-rose-300 bg-rose-50" : "border-emerald-300 bg-emerald-50"}`}>
                <b>PII-tier audit:</b> {usage.data.pii_audit.pii_calls_ok} PII calls ok, {usage.data.pii_audit.pii_to_training_tier} went to a training-tier model
                {usage.data.pii_audit.pii_to_training_tier > 0 && (
                  <pre className="mt-1 overflow-auto text-[10px]">{JSON.stringify(usage.data.pii_audit.offenders, null, 2)}</pre>
                )}
              </div>
            </div>
          )}
        </Widget>
      </section>
    </div>
  );
}
