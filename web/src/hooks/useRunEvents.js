import { useEffect, useRef, useState } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE } from "../api/client.js";

const EMPTY = {
  plan: null,
  steps: {}, // step_id -> {id, tool, llm_task, status, attempts, latency_ms, model_id, shadow_cost_usd, fallback}
  verify: null,
  critics: [],
  judge: null,
  ledgerEntries: [],
  ledgerHead: null,
  clarify: null,
  result: null,
  error: null,
  raw: [],
};

/** Live SSE consumer for `GET /runs/{run_id}/events` (app/agent/CONTRACT.md §2), used by the
 * Agent console's PlanPanel/LedgerDrawer/ResultTable/MapView (phase6 SKILL). */
export function useRunEvents(runId) {
  const [state, setState] = useState(EMPTY);
  const [connection, setConnection] = useState("idle"); // idle | connecting | open | closed | error
  const ctrlRef = useRef(null);

  useEffect(() => {
    if (!runId) {
      setState(EMPTY);
      setConnection("idle");
      return;
    }
    setState(EMPTY);
    setConnection("connecting");
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;

    fetchEventSource(`${API_BASE}/runs/${runId}/events`, {
      signal: ctrl.signal,
      openWhenHidden: true,
      async onopen(res) {
        if (res.ok) setConnection("open");
        else setConnection("error");
      },
      onmessage(ev) {
        let envelope;
        try {
          envelope = JSON.parse(ev.data);
        } catch {
          return;
        }
        applyEvent(ev.event || envelope.type, envelope, setState);
      },
      onerror(err) {
        setConnection("error");
        throw err; // stop retrying — the run is either done or the API is down
      },
      onclose() {
        setConnection((c) => (c === "error" ? c : "closed"));
      },
    }).catch(() => setConnection("error"));

    return () => ctrl.abort();
  }, [runId]);

  return { ...state, connection };
}

function applyEvent(type, envelope, setState) {
  const data = envelope.data || {};
  setState((s) => {
    const raw = [...s.raw, envelope];
    switch (type) {
      case "plan":
        return { ...s, raw, plan: data.plan, planModel: data.model_id, planAttempt: data.attempt, planRepaired: data.repaired };
      case "step_start":
        return { ...s, raw, steps: { ...s.steps, [data.step_id]: { ...(s.steps[data.step_id] || {}), id: data.step_id, tool: data.tool, llm_task: data.llm_task, args_preview: data.args_preview, status: "running", attempt: data.attempt } } };
      case "fallback":
        return { ...s, raw, steps: { ...s.steps, [data.step_id]: { ...(s.steps[data.step_id] || {}), fallback: data } } };
      case "step_end":
        // contract v1.1: `route` = this step's own LLM calls (candidates/scores/filtered/attempts/tiers/cost).
        return { ...s, raw, steps: { ...s.steps, [data.step_id]: { ...(s.steps[data.step_id] || {}), status: data.status, output_ref: data.output_ref, summary: data.summary, n_rows: data.n_rows, latency_ms: data.latency_ms, model_id: data.model_id, shadow_cost_usd: data.shadow_cost_usd, actual_cost_usd: data.actual_cost_usd, tokens_in: data.tokens_in, tokens_out: data.tokens_out, route: data.route } } };
      case "verify":
        return { ...s, raw, verify: data };
      case "critic":
        return { ...s, raw, critics: [...s.critics, data] };
      case "judge":
        return { ...s, raw, judge: data };
      case "clarify":
        return { ...s, raw, clarify: data };
      case "ledger":
        return { ...s, raw, ledgerEntries: [...s.ledgerEntries, data], ledgerHead: data.hash ?? s.ledgerHead };
      case "result":
        return { ...s, raw, result: data };
      case "error":
        return { ...s, raw, error: data };
      default:
        return { ...s, raw };
    }
  });
}
