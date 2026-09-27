import { useEffect, useState } from "react";

// The agent's stages in plain words (backend nodes: intake → planner → execute → verify → critic → judge → present)
const STAGES = [
  { key: "intake", label: "Understanding the question" },
  { key: "plan", label: "Making a plan" },
  { key: "execute", label: "Looking up the data" },
  { key: "verify", label: "Checking the numbers" },
  { key: "critic", label: "Second opinion from another AI company" },
  { key: "judge", label: "Deciding what to trust" },
  { key: "present", label: "Writing the answer" },
];

function stageDone(run) {
  const nodes = new Set((run.ledgerEntries || []).map((e) => e.node));
  const planSteps = run.plan?.steps?.filter((s) => s.tool) || [];
  const stepsDone = planSteps.length > 0 && planSteps.every((s) => ["ok", "failed", "skipped", "error"].includes(run.steps?.[s.id]?.status));
  return {
    intake: !!run.plan || nodes.has("intake"),
    plan: !!run.plan,
    execute: !!run.verify || stepsDone || nodes.has("execute"),
    verify: !!run.verify || nodes.has("verify"),
    critic: !!run.judge || nodes.has("critic"),
    judge: !!run.judge || nodes.has("judge"),
    present: !!run.result,
  };
}

function detailFor(key, run) {
  if (key === "plan" && run.plan) return run.plan.goal;
  if (key === "execute" && run.plan) {
    const tools = run.plan.steps.filter((s) => s.tool);
    const done = tools.filter((s) => ["ok", "failed", "skipped"].includes(run.steps?.[s.id]?.status)).length;
    return `${done} of ${tools.length} data step(s) done${tools.length ? ` (${tools.map((s) => s.tool.replace(/_/g, " ")).join(", ")})` : ""}`;
  }
  if (key === "verify" && run.verify) return `${run.verify.passed} of ${run.verify.claims_checked} checks passed`;
  if (key === "critic" && run.critics?.length) {
    const c = run.critics.find((x) => !x.skipped);
    return c ? `${c.vendor || "another vendor"} tried to disprove a claim: ${c.result?.refuted ? "it found a problem" : "the claim held"}` : "skipped";
  }
  if (key === "judge" && run.judge) {
    const counts = {};
    (run.judge.verdicts || []).forEach((v) => { counts[v.verdict] = (counts[v.verdict] || 0) + 1; });
    return Object.entries(counts).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(" · ");
  }
  return null;
}

/** Vertical progress timeline for one agent run: grey = waiting, pulsing blue = working, green = done, red = stopped. */
export function RunProgress({ run, startedAt }) {
  const finished = !!(run.result || run.error || run.clarify);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (finished) return undefined;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [finished]);
  const [endedAt, setEndedAt] = useState(null);
  useEffect(() => { if (finished && !endedAt) setEndedAt(Date.now()); }, [finished, endedAt]);
  useEffect(() => { setEndedAt(null); }, [startedAt]);

  const done = stageDone(run);
  const current = STAGES.find((s) => !done[s.key])?.key;
  const secs = startedAt ? Math.max(0, Math.round(((endedAt || now) - startedAt) / 1000)) : null;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4" data-testid="run-progress">
      <div className="mb-3 flex items-center gap-2">
        {!finished ? (
          <>
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
            <span className="text-sm font-semibold text-sky-700">Working…{secs !== null ? ` ${secs}s` : ""}</span>
          </>
        ) : run.error ? (
          <span className="text-sm font-semibold text-rose-700">Stopped with an error{secs !== null ? ` after ${secs}s` : ""}</span>
        ) : run.clarify ? (
          <span className="text-sm font-semibold text-amber-700">Needs more information</span>
        ) : (
          <span className="text-sm font-semibold text-emerald-700">✓ Done{secs !== null ? ` in ${secs}s` : ""}</span>
        )}
      </div>
      <ol className="relative">
        {STAGES.map((s, i) => {
          const isDone = done[s.key];
          const isCurrent = !isDone && s.key === current;
          const failed = isCurrent && run.error;
          const state = isDone ? "done" : failed ? "failed" : isCurrent && !finished ? "running" : "waiting";
          const detail = detailFor(s.key, run);
          return (
            <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0" data-testid={`stage-${s.key}`} data-state={state}>
              {i < STAGES.length - 1 && (
                <span className={`absolute left-[7px] top-4 h-full w-0.5 ${isDone ? "bg-emerald-400" : "bg-gray-200"}`} />
              )}
              <span className="relative mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
                {state === "running" && <span className="absolute inline-flex h-4 w-4 animate-ping rounded-full bg-sky-400 opacity-60" />}
                <span className={`relative inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-white ${
                  state === "done" ? "bg-emerald-500" : state === "running" ? "bg-sky-500" : state === "failed" ? "bg-rose-500" : "bg-gray-300"}`}>
                  {state === "done" ? "✓" : state === "failed" ? "!" : ""}
                </span>
              </span>
              <div className="min-w-0">
                <div className={`text-sm ${state === "waiting" ? "text-gray-400" : "font-medium text-gray-800"}`}>{s.label}</div>
                {detail && <div className="mt-0.5 text-xs text-gray-500">{detail}</div>}
                {failed && <div className="mt-0.5 text-xs text-rose-600">{run.error.message}</div>}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
