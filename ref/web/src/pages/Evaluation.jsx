import { useMemo, useState } from "react";
import { EvalSummaryResponseSchema, DecisionsResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { useStore } from "../store/useStore.js";
import { t } from "../i18n/labels.js";

const LIMITATIONS = [
  "Findings are leads for field verification, not legal conclusions — especially single-season post-possession signals.",
  "10 m Sentinel-2 pixels under-detect ploughing (only 16 of 1,144 parcel-seasons show a clear signal).",
  "Almost every parcel greens up after the NE monsoon regardless of acquisition — \"green after possession\" alone does not prove farming (D-035); we compare against a control group instead.",
  "The land-use student model is weak on rare classes (e.g. irrigated_multi has only 5 training examples).",
  "FMB polygons are digitised sketches — overlaps/slivers affect area comparisons (D-023).",
  "OCR/extraction accuracy on scanned Tamil documents is well under 100%; every value carries a confidence and evidence link, and low-confidence rows route to the review queue.",
  "The live agent graph (app.agent) is not wired in yet for every query shape — some Agent console runs fall back to a deterministic stub (visible in the run's narrative caveat).",
  "AWS spend is tracked manually (`make aws-cost`) against a $15 self-imposed cap, not enforced by AWS itself.",
];

/** Page 8: extraction/classifier/matcher/student/agent-eval numbers, honesty log, limitations, decisions. */
export function Evaluation() {
  const lang = useStore((s) => s.lang);
  const evalSummary = useApi("/eval/summary", { schema: EvalSummaryResponseSchema });
  const decisions = useApi("/decisions", { schema: DecisionsResponseSchema });
  const [metricQuery, setMetricQuery] = useState("");
  const [decisionQuery, setDecisionQuery] = useState("");

  const filteredRows = useMemo(() => {
    const rows = evalSummary.data?.rows || [];
    if (!metricQuery) return rows;
    const q = metricQuery.toLowerCase();
    return rows.filter((r) => Object.values(r).some((v) => String(v).toLowerCase().includes(q)));
  }, [evalSummary.data, metricQuery]);

  const filteredDecisions = useMemo(() => {
    const items = decisions.data?.items || [];
    if (!decisionQuery) return items;
    const q = decisionQuery.toLowerCase();
    return items.filter((d) => `${d.id} ${d.decision} ${d.by} ${d.context}`.toLowerCase().includes(q));
  }, [decisions.data, decisionQuery]);

  const files = evalSummary.data?.files || {};

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Evaluation &amp; honesty</h1>
      <p className="text-xs text-gray-500">
        Everything below is parsed live from docs/metrics.md, docs/decisions.md and the stored eval JSONs (`app/api/routers/meta.py`) — never hand-copied, so it cannot drift from the measured record.
      </p>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Metrics (extraction bake-off, classifier, matcher, satellite, agent — heldout/golden history)</h2>
        <input value={metricQuery} onChange={(e) => setMetricQuery(e.target.value)} placeholder="filter…" className="mb-2 w-64 rounded border border-gray-300 px-2 py-1 text-xs" />
        <Widget status={evalSummary.status} error={evalSummary.error} onRetry={evalSummary.reload} title="Metrics" minHeight="8rem">
          {filteredRows.length > 0 ? (
            <DataTable
              columns={Object.keys(filteredRows[0]).map((k) => ({ key: k, label: k }))}
              rows={filteredRows}
              rowKey={(r) => JSON.stringify(r)}
              csvName="metrics"
            />
          ) : (
            <p className="text-xs text-gray-400">No metric rows match.</p>
          )}
        </Widget>
      </section>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <FileCard title="Satellite land-use student (LightGBM, leave-one-village-out)" data={files.student_metrics} pick={["lovo_accuracy", "lovo_macro_f1", "n_train", "accept_rate_at_tau", "accuracy_when_accepted"]} />
        <FileCard title="Agent evaluation (8 end-to-end queries, live free models)" data={files.agent_run_final?.summary} pick={["n", "plan_validity", "tool_success", "answer_match_rate", "verified_claim_rate", "critic_challenges_run", "p50_wall_s", "shadow_usd_total", "actual_usd_total"]} />
        <FileCard title="Control-group DiD (planet)" data={files.planet_did} pick={["version", "generated"]} raw />
        <FileCard title="Matcher (facts/events linked to parcel_uid)" data={files.match_eval} pick={["facts_by_village", "facts_by_doc_type", "events_by_village", "events_by_stage"]} raw />
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">{t("limitations", lang)}</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-amber-800">
          {LIMITATIONS.map((l, i) => <li key={i}>{l}</li>)}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Decision log (docs/decisions.md, D-001…)</h2>
        <input value={decisionQuery} onChange={(e) => setDecisionQuery(e.target.value)} placeholder="search decisions…" className="mb-2 w-64 rounded border border-gray-300 px-2 py-1 text-xs" />
        <Widget status={decisions.status} error={decisions.error} onRetry={decisions.reload} title="Decisions" minHeight="8rem">
          <ul className="max-h-96 space-y-1 overflow-auto text-xs">
            {filteredDecisions.map((d) => (
              <li key={d.id} className="rounded border border-gray-200 p-2">
                <div className="font-medium text-gray-700">{d.id} — {d.decision}</div>
                <div className="text-gray-400">{d.date} · {d.by}</div>
                {d.context && <div className="mt-0.5 text-gray-500">{d.context}</div>}
              </li>
            ))}
          </ul>
        </Widget>
      </section>
    </div>
  );
}

function FileCard({ title, data, pick, raw }) {
  const [open, setOpen] = useState(false);
  if (!data) return (
    <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3 text-xs text-gray-400">
      <div className="font-medium text-gray-500">{title}</div>
      not available (eval file missing on disk)
    </div>
  );
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs">
      <div className="mb-1 font-medium text-gray-700">{title}</div>
      {!raw && pick && (
        <div className="flex flex-wrap gap-2">
          {pick.filter((k) => data[k] !== undefined).map((k) => (
            <span key={k} className="rounded bg-gray-100 px-2 py-0.5">{k}: <b>{typeof data[k] === "number" ? data[k].toFixed(3) : String(data[k])}</b></span>
          ))}
        </div>
      )}
      <button onClick={() => setOpen((o) => !o)} className="mt-2 text-emerald-700 underline">{open ? "hide" : "show"} raw JSON</button>
      {open && <pre className="mt-1 max-h-64 overflow-auto rounded bg-gray-50 p-2 text-[10px]">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}
