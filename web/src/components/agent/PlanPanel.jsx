import { useState } from "react";
import { VerdictBadge } from "../common/Badge.jsx";
import { fmtUsd } from "../../lib/format.js";

/**
 * Live plan + per-step model/router/verify/critic/judge view driven by `useRunEvents`
 * (phase6 SKILL: PlanPanel). Contract v1.1: `step_end`/`critic`/`judge` carry a live `route`
 * (router attempts: candidates, scores, filtered reasons, choice, attempts, latency, tokens,
 * shadow/actual cost, privacy tier requested/effective) — the "why this model" tooltip reads
 * that live, falling back to `GET /runs/{id}/trace` for older/replayed runs.
 */
export function PlanPanel({ run, trace }) {
  if (!run.plan && !run.error && !run.clarify) {
    return <p className="text-sm text-gray-400">Waiting for the plan…</p>;
  }
  const routeTrace = trace?.route_trace?.length ? trace.route_trace : run.result?.workspace?.route_trace;
  return (
    <div className="space-y-3" data-testid="plan-panel">
      {run.clarify && (
        <div className="rounded border border-sky-200 bg-sky-50 p-2 text-sm text-sky-800">
          Agent needs clarification: <b>{run.clarify.question}</b> (missing: {run.clarify.missing?.join(", ")})
        </div>
      )}
      {run.error && (
        <div className="rounded border border-rose-200 bg-rose-50 p-2 text-sm text-rose-800">{run.error.message}</div>
      )}
      {run.plan && (
        <div>
          <div className="mb-1 text-xs text-gray-400">
            goal: <span className="text-gray-600">{run.plan.goal}</span> · planner model: {run.planModel} {run.planRepaired ? "(repaired)" : ""}
          </div>
          <ol className="space-y-2">
            {run.plan.steps.map((step) => (
              <StepRow key={step.id} step={step} live={run.steps[step.id]} routeTrace={routeTrace} routerLog={trace?.router_log} />
            ))}
          </ol>
        </div>
      )}
      {run.verify && (
        <Section title={`Verifier — ${run.verify.passed}/${run.verify.claims_checked} checks passed`}>
          <ul className="space-y-1 text-xs text-gray-600">
            {(run.verify.checks || []).map((c, i) => (
              <li key={i} className={c.passed ? "text-emerald-700" : "text-rose-700"}>
                {c.passed ? "✓" : "✗"} {c.name} <span className="text-gray-400">({c.claim_id})</span> — {c.detail}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {run.critics.length > 0 && (
        <Section title="Critic challenges">
          <ul className="space-y-2 text-xs text-gray-600">
            {run.critics.map((c, i) => (
              <li key={i} className="rounded border border-gray-200 p-2">
                {c.skipped ? (
                  <span className="text-gray-400">skipped — {c.reason}</span>
                ) : (
                  <>
                    <div><b>vendor:</b> {c.vendor || c.producer_vendor || "—"} · <b>model:</b> {c.model_id || "—"}</div>
                    <div><b>hypothesis:</b> {c.hypothesis}</div>
                    <div><b>test:</b> {c.check?.tool} {JSON.stringify(c.check?.args)}</div>
                    <div><b>outcome:</b> {c.result?.refuted ? "refuted" : "held"} — {c.result?.detail}</div>
                    <RouteDetails label="critic model call" entries={c.route} />
                    <RouteDetails label="check's own LLM calls" entries={c.check_route} />
                  </>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {run.judge && (
        <Section title={`Judge verdicts${run.judge.n_claims !== undefined ? ` (${run.judge.n_claims} claims)` : ""}`}>
          <ul className="space-y-1 text-xs">
            {(run.judge.verdicts || []).map((v, i) => (
              <li key={i} className="flex items-center gap-2">
                <VerdictBadge verdict={v.verdict} /> <span className="text-gray-400">{v.claim_id}</span> — {v.reason}
              </li>
            ))}
          </ul>
          {run.judge.model_id == null && <p className="mt-1 text-[11px] text-gray-400">no judge model recorded for this run</p>}
          <RouteDetails label="judge model call(s)" entries={run.judge.route} />
        </Section>
      )}
    </div>
  );
}

function StepRow({ step, live, routeTrace, routerLog }) {
  const [open, setOpen] = useState(false);
  const liveRoute = live?.route?.length ? live.route : null;
  const rt = !liveRoute ? routeTrace?.find((r) => r.step_id === step.id) : null;
  const logRows = !liveRoute && !rt ? routerLog?.filter((r) => r.step_id === step.id) || [] : [];
  const status = live?.status || "pending";
  const color = status === "ok" ? "border-emerald-300 bg-emerald-50" : status === "running" ? "border-sky-300 bg-sky-50" : status === "failed" ? "border-rose-300 bg-rose-50" : "border-gray-200 bg-gray-50";
  return (
    <li className={`rounded border p-2 text-xs ${color}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-gray-500">{step.id}</span>
        <span className="font-medium">{step.tool || step.llm_task}</span>
        <span className="rounded bg-white px-1.5 py-0.5 text-[10px] text-gray-500">{status}</span>
        {live?.model_id && <span className="text-gray-500">model: {live.model_id}</span>}
        {live?.latency_ms !== undefined && <span className="text-gray-500">{live.latency_ms} ms</span>}
        {live?.tokens_in !== undefined && <span className="text-gray-500">{live.tokens_in}→{live.tokens_out} tok</span>}
        {live?.shadow_cost_usd !== undefined && <span className="text-gray-500">shadow {fmtUsd(live.shadow_cost_usd)}</span>}
        {live?.actual_cost_usd !== undefined && <span className="text-gray-500">actual {fmtUsd(live.actual_cost_usd)}</span>}
        {live?.fallback && <span className="rounded bg-amber-200 px-1.5 py-0.5 font-medium text-amber-900">fallback: {live.fallback.from} → {live.fallback.to} ({live.fallback.reason})</span>}
        <button onClick={() => setOpen((o) => !o)} className="ml-auto text-emerald-700 underline">why?</button>
      </div>
      {open && (
        <div className="mt-1 space-y-2 rounded bg-white p-2 text-[11px] text-gray-600">
          {liveRoute ? (
            liveRoute.map((r, i) => <RouteEntry key={i} entry={r} />)
          ) : rt ? (
            <RouteEntry entry={rt} />
          ) : logRows.length ? (
            <div>
              <div className="text-gray-400">no router candidates/scores recorded for this step — showing the router log instead:</div>
              {logRows.map((r, i) => (
                <div key={i}>{r.task}: <b>{r.model_id}</b> — {r.outcome} · {r.latency_ms} ms · shadow {fmtUsd(r.shadow_cost_usd)}</div>
              ))}
            </div>
          ) : (
            <span className="text-gray-400">No router trace for this step — it may be a pure tool call with no LLM route, or the run used the deterministic stub (see the narrative caveat).</span>
          )}
        </div>
      )}
    </li>
  );
}

/** One `RouteTraceEntry` (contract v1.1): candidates, per-model scores, filtered-out reasons,
 * attempts (with per-attempt outcome/error/latency/tokens), and privacy tiers. */
function RouteEntry({ entry }) {
  if (!entry) return null;
  return (
    <div className="border-b border-gray-100 pb-1 last:border-0">
      <div><b>chosen:</b> {entry.choice || "—"} — {entry.reason || "no reason recorded"}</div>
      {entry.privacy_tier && (
        <div><b>privacy tier:</b> requested {entry.privacy_tier}{entry.effective_tier && entry.effective_tier !== entry.privacy_tier ? ` → effective ${entry.effective_tier}` : ""}</div>
      )}
      {entry.candidates?.length ? <div><b>candidates:</b> {entry.candidates.join(", ")}</div> : null}
      {entry.scores && Object.keys(entry.scores).length ? (
        <div><b>scores:</b> {Object.entries(entry.scores).map(([m, sc]) => `${m}=${typeof sc === "number" ? sc.toFixed(3) : sc}`).join(", ")}</div>
      ) : null}
      {entry.filtered?.length ? (
        <div><b>filtered out:</b> {entry.filtered.map((f, i) => <span key={i} className="mr-1">{f.id}({f.reason})</span>)}</div>
      ) : null}
      {entry.attempts?.length ? (
        <div>
          <b>attempts:</b>
          <ul className="ml-3 list-disc">
            {entry.attempts.map((a, i) => (
              <li key={i} className={a.outcome !== "ok" ? "text-amber-700" : ""}>
                {a.model_id}: {a.outcome}{a.error_kind ? ` (${a.error_kind})` : ""} · {a.latency_ms}ms{a.tokens_in !== undefined ? ` · ${a.tokens_in}→${a.tokens_out} tok` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="text-gray-400">
        {entry.latency_ms !== undefined && `${entry.latency_ms}ms `}
        {entry.tokens_in !== undefined && `· ${entry.tokens_in}→${entry.tokens_out} tok `}
        {entry.shadow_cost_usd !== undefined && `· shadow ${fmtUsd(entry.shadow_cost_usd)} `}
        {entry.actual_cost_usd !== undefined && `· actual ${fmtUsd(entry.actual_cost_usd)}`}
      </div>
    </div>
  );
}

function RouteDetails({ label, entries }) {
  if (!entries?.length) return null;
  return (
    <div className="mt-1 rounded bg-gray-50 p-1.5 text-[11px]">
      <div className="font-medium text-gray-500">{label}:</div>
      {entries.map((e, i) => <RouteEntry key={i} entry={e} />)}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</h4>
      {children}
    </div>
  );
}
