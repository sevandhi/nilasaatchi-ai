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
import { RunProgress } from "../components/agent/RunProgress.jsx";
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
  const [startedAt, setStartedAt] = useState(null);   // for the live timer (null for replayed history runs)

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
    setStartedAt(Date.now());
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
      </div>

      {runId && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[19rem_1fr]">
          <div className="self-start lg:sticky lg:top-2">
            <RunProgress run={run} startedAt={startedAt} />
          </div>
          <div className="min-w-0 space-y-4">
            {workspace ? (
              <AnswerCard workspace={workspace} />
            ) : run.clarify ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                <b>The agent needs more information:</b> {run.clarify.question}
              </div>
            ) : run.error ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{run.error.message}</div>
            ) : (
              <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6" data-testid="answer-placeholder">
                <div className="mb-3 text-sm text-gray-500">The answer will appear here when the agent finishes (usually 30–60 seconds).</div>
                <div className="space-y-2">
                  <div className="h-3 w-3/4 animate-pulse rounded bg-gray-200" />
                  <div className="h-3 w-full animate-pulse rounded bg-gray-200" />
                  <div className="h-3 w-5/6 animate-pulse rounded bg-gray-200" />
                </div>
              </div>
            )}

            {(run.verify || run.critics.length > 0 || run.judge) && <CheckSummary run={run} />}

            <details className="group rounded-xl border border-gray-200 bg-white" data-testid="technical-details">
              <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-gray-700">
                Technical details <span className="font-normal text-gray-400">(plan steps, model choices, SQL, audit ledger)</span>
              </summary>
              <div className="space-y-4 border-t px-4 py-3">
                <PlanPanel run={run} trace={trace} />
                <div>
                  <h3 className="mb-1 text-xs font-semibold uppercase text-gray-500">SQL / GIS operations</h3>
                  <SqlPanel entries={workspace?.sql} />
                </div>
                <div>
                  <h3 className="mb-1 text-xs font-semibold uppercase text-gray-500">Audit ledger</h3>
                  <LedgerDrawer runId={runId} entries={run.ledgerEntries} head={trace?.ledger_head || run.ledgerHead} />
                </div>
                <p className="font-mono text-[11px] text-gray-400">run {runId} · live connection: {run.connection}</p>
              </div>
            </details>
          </div>
        </div>
      )}

      <details className="rounded-xl border border-gray-200 bg-white p-3" open={!runId}>
        <summary className="cursor-pointer select-none text-sm font-semibold text-gray-600">Earlier runs{history.data ? ` (${history.data.items?.length || 0})` : ""}</summary>
        <div className="mt-2">
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
            onRowClick={(r) => { setStartedAt(null); setRunId(r.id); }}
            rowKey={(r) => r.id}
            selectedKey={runId}
            csvName="run-history"
          />
        </Widget>
        </div>
      </details>
    </div>
  );
}

// "[c_s1_0]" claim markers in the narrative -> small grey tags
function Narrative({ text }) {
  const parts = String(text || "").split(/(\[c_[a-z0-9_]+\])/i);
  return (
    <p className="text-[15px] leading-relaxed text-gray-800">
      {parts.map((p, i) => (/^\[c_/i.test(p)
        ? <span key={i} className="mx-0.5 rounded bg-gray-100 px-1 align-middle font-mono text-[10px] text-gray-500" title="claim id (see Technical details)">{p.slice(1, -1)}</span>
        : <span key={i}>{p}</span>))}
    </p>
  );
}

function AnswerCard({ workspace }) {
  return (
    <div className="space-y-4 rounded-xl border border-emerald-200 bg-white p-5 shadow-sm" data-testid="answer-card">
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Answer</div>
        <h2 className="mt-0.5 text-lg font-semibold text-gray-900">{workspace.title}</h2>
      </div>
      {workspace.narrative?.en && <Narrative text={workspace.narrative.en} />}
      {workspace.kpis?.length ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {workspace.kpis.map((k) => <KpiCard key={k.id} kpi={k} />)}
        </div>
      ) : null}
      {workspace.charts?.map((c) => <ChartWidget key={c.id} spec={c} />)}
      {workspace.tables?.map((t) => (
        <div key={t.id}>
          <h3 className="mb-1 text-xs font-semibold uppercase text-gray-500">{t.title}</h3>
          <DataTable columns={t.columns} rows={t.rows} csvName={t.id} />
        </div>
      ))}
      {workspace.map?.layers?.length ? (
        <MapView layers={workspace.map.layers.map((l) => ({ ...l, kind: l.kind === "parcels" ? "parcels" : "reference" }))} height="22rem" />
      ) : null}
      {workspace.caveats?.length ? (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <b>Keep in mind:</b>
          <ul className="mt-0.5 list-disc pl-4">{workspace.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

const human = (s) => String(s || "").replace(/_/g, " ");
const VERDICT_TEXT = { ACCEPT: "accepted: the checks support it", REVIEW: "needs a person to look", DOWNGRADE: "kept, but with lower confidence", REROUTE: "re-checked with another route" };

/** Plain-language summary of how the answer was checked: automatic checks, the second opinion, the verdicts. */
function CheckSummary({ run }) {
  const critic = run.critics.find((c) => !c.skipped);
  const counts = {};
  (run.judge?.verdicts || []).forEach((v) => { counts[v.verdict] = (counts[v.verdict] || 0) + 1; });
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4" data-testid="check-summary">
      <h3 className="mb-3 text-sm font-semibold text-gray-700">How the answer was checked</h3>
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg bg-gray-50 p-3">
          <div className="text-xs font-semibold uppercase text-gray-500">Automatic checks</div>
          {run.verify ? (
            <>
              <div className={`mt-1 text-base font-semibold ${run.verify.passed === run.verify.claims_checked ? "text-emerald-700" : "text-amber-700"}`}>
                {run.verify.passed} of {run.verify.claims_checked} passed
              </div>
              <ul className="mt-1 space-y-0.5 text-xs text-gray-600">
                {(run.verify.checks || []).slice(0, 4).map((c, i) => <li key={i}>{c.passed ? "✓" : "✗"} {human(c.name)}</li>)}
              </ul>
            </>
          ) : <div className="mt-1 text-xs text-gray-400">running…</div>}
        </div>
        <div className="rounded-lg bg-gray-50 p-3">
          <div className="text-xs font-semibold uppercase text-gray-500">Second opinion</div>
          {critic ? (
            <>
              <div className={`mt-1 text-base font-semibold ${critic.result?.refuted ? "text-rose-700" : "text-emerald-700"}`}>
                {critic.result?.refuted ? "Found a problem" : "Claim held"}
              </div>
              <p className="mt-1 text-xs text-gray-600">
                A model from a different company ({critic.vendor || "another vendor"}) tried to prove the answer wrong: <i>&ldquo;{critic.hypothesis}&rdquo;</i>
              </p>
            </>
          ) : <div className="mt-1 text-xs text-gray-400">{run.critics.length ? "skipped" : "running…"}</div>}
        </div>
        <div className="rounded-lg bg-gray-50 p-3">
          <div className="text-xs font-semibold uppercase text-gray-500">Final verdicts</div>
          {run.judge ? (
            <ul className="mt-1 space-y-0.5 text-xs text-gray-700">
              {Object.entries(counts).map(([k, n]) => (
                <li key={k}><b>{n}</b> {n === 1 ? "claim" : "claims"} {VERDICT_TEXT[k] || k.toLowerCase()}</li>
              ))}
            </ul>
          ) : <div className="mt-1 text-xs text-gray-400">running…</div>}
        </div>
      </div>
    </div>
  );
}
