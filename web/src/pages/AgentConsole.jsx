import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ExamplesResponseSchema, RunListResponseSchema, RunTraceResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { useRunEvents } from "../hooks/useRunEvents.js";
import { apiFetch, READ_ONLY } from "../api/client.js";
import { Widget } from "../components/common/Widget.jsx";
import { PlanPanel } from "../components/agent/PlanPanel.jsx";
import { SqlPanel } from "../components/agent/SqlPanel.jsx";
import { LedgerDrawer } from "../components/agent/LedgerDrawer.jsx";
import { KpiCard } from "../components/common/KpiCard.jsx";
import { ChartWidget } from "../components/charts/ChartWidget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { MapView } from "../components/map/MapView.jsx";
import { useStore } from "../store/useStore.js";
import { fmtDate } from "../lib/format.js";

/** Page 5: agent console — NL command box, live plan/route/verify/critic/judge, ledger,
 * run history, chaos toggle. */
export function AgentConsole() {
  const [params] = useSearchParams();
  const examples = useApi(READ_ONLY ? null : "/examples", { schema: ExamplesResponseSchema });
  const history = useApi("/runs", { schema: RunListResponseSchema, params: { limit: 20 } }, []);
  const { lang, chaos } = useStore((s) => s);
  const [text, setText] = useState(params.get("q") || "");
  const [runId, setRunId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const run = useRunEvents(runId);
  const autoSubmitted = useRef(false);
  const [trace, setTrace] = useState(null);

  async function submit(requestText) {
    setSubmitting(true);
    setSubmitError(null);
    setTrace(null);
    // `chaos` (e.g. "gemini:down") is consumed by app.router.chaos.set_chaos() server-side
    // (see its docstring: "a runtime override (e.g. the UI toggle) can be set with set_chaos()")
    // but `POST /runs` does not accept it yet — sent anyway so this is forward-compatible; it is
    // silently ignored by the current API. The trailing text keyword still triggers the
    // deterministic stub's fallback when AGENT_FORCE_STUB=1 (tests/api).
    const body = {
      request: chaos ? `${requestText} (chaos: gemini down)` : requestText,
      workspace_id: null,
      domain: null,
      lang,
      ...(chaos ? { chaos: "gemini:down" } : {}),
    };
    const res = await apiFetch("/runs", { method: "POST", body });
    setSubmitting(false);
    if (!res.ok) {
      setSubmitError(res.message);
      return;
    }
    setRunId(res.data.run_id);
  }

  useEffect(() => {
    if (!READ_ONLY && params.get("q") && !autoSubmitted.current) {
      autoSubmitted.current = true;
      submit(params.get("q"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Once the run finishes, fetch the persisted trace (router candidates/scores per step,
  // critic/judge detail, ledger head) — richer than the live SSE stream alone (see
  // GET /runs/{id}/trace, app/api/routers/runs.py).
  useEffect(() => {
    if (!runId || !(run.result || run.error)) return;
    let cancelled = false;
    apiFetch(`/runs/${runId}/trace`, { schema: RunTraceResponseSchema }).then((res) => {
      if (!cancelled && res.ok) setTrace(res.data);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, run.result, run.error]);

  const workspace = run.result?.workspace;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Agent console</h1>

      <div className="rounded-lg border border-gray-200 bg-white p-3">
        {READ_ONLY ? (
          <p className="text-xs text-gray-500">
            Browse past runs in the history below and replay their plan, evidence and ledger. Asking new questions of
            the live AI agent is available in the full application.
          </p>
        ) : (
          <>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (text.trim()) submit(text);
              }}
              className="flex gap-2"
            >
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Ask in English or தமிழ்…"
                className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-sm"
                data-testid="agent-command-input"
              />
              <button type="submit" disabled={submitting} className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40" data-testid="agent-command-submit">
                {submitting ? "Running…" : "Run"}
              </button>
            </form>
            {submitError && <p className="mt-1 text-xs text-rose-600">{submitError}</p>}
            <Widget status={examples.status} error={examples.error} onRetry={examples.reload} title="Examples" minHeight="2rem">
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(examples.data?.items || []).map((ex) => (
                  <button key={ex.id} onClick={() => { setText(ex.text); submit(ex.text); }} className="rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50" data-testid="example-chip">
                    {ex.text}
                  </button>
                ))}
              </div>
            </Widget>
          </>
        )}
        {runId && <p className="mt-2 font-mono text-[11px] text-gray-400">run: {runId} · SSE: {run.connection}</p>}
      </div>

      {runId && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-gray-200 bg-white p-3">
            <h2 className="mb-2 text-sm font-semibold text-gray-600">Live plan &amp; verification</h2>
            <PlanPanel run={run} trace={trace} />
          </div>
          <div className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-white p-3">
              <h2 className="mb-2 text-sm font-semibold text-gray-600">SQL / GIS operations</h2>
              <SqlPanel entries={workspace?.sql} />
            </div>
            <div className="rounded-lg border border-gray-200 bg-white p-3">
              <h2 className="mb-2 text-sm font-semibold text-gray-600">Ledger</h2>
              <LedgerDrawer runId={runId} entries={run.ledgerEntries} head={trace?.ledger_head || run.ledgerHead} />
            </div>
          </div>
        </div>
      )}

      {workspace && (
        <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-3">
          <h2 className="text-sm font-semibold text-gray-600">Result: {workspace.title}</h2>
          {workspace.narrative?.en && <p className="text-sm text-gray-700">{workspace.narrative.en}</p>}
          {workspace.caveats?.length ? (
            <ul className="list-disc pl-4 text-xs text-amber-700">
              {workspace.caveats.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          ) : null}
          {workspace.kpis?.length ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {workspace.kpis.map((k) => <KpiCard key={k.id} kpi={k} />)}
            </div>
          ) : null}
          {workspace.charts?.map((c) => <ChartWidget key={c.id} spec={c} />)}
          {workspace.tables?.map((t) => (
            <div key={t.id}>
              <h3 className="mb-1 text-xs font-semibold text-gray-500">{t.title}</h3>
              <DataTable columns={t.columns} rows={t.rows} csvName={t.id} />
            </div>
          ))}
          {workspace.map?.layers?.length ? (
            <MapView layers={workspace.map.layers.map((l) => ({ ...l, kind: l.kind === "parcels" ? "parcels" : "reference" }))} height="24rem" />
          ) : null}
        </div>
      )}

      <div className="rounded-lg border border-gray-200 bg-white p-3">
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Run history</h2>
        <Widget status={history.status} error={history.error} onRetry={history.reload} title="Run history" minHeight="4rem" emptyReason="No runs recorded yet.">
          <DataTable
            columns={[
              { key: "id", label: "Run" },
              { key: "status", label: "Status" },
              { key: "request", label: "Request" },
              { key: "created_at", label: "Created" },
              { key: "updated_at", label: "Updated" },
            ]}
            rows={(history.data?.items || []).map((r) => ({ ...r, created_at: fmtDate(r.created_at) + " " + new Date(r.created_at).toLocaleTimeString(), updated_at: fmtDate(r.updated_at) }))}
            onRowClick={(r) => setRunId(r.id)}
            rowKey={(r) => r.id}
            selectedKey={runId}
            csvName="run-history"
          />
        </Widget>
      </div>
    </div>
  );
}
