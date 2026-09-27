import { useState } from "react";
import { apiFetch, READ_ONLY } from "../../api/client.js";

/**
 * Hash-chain ledger for the run + a Verify button that calls the API
 * (phase6 SKILL: LedgerDrawer). `POST /ledger/verify?run_id=` is listed in the P6 ui-spec's
 * "Backend additions needed" and is not implemented yet — the button reports that plainly
 * rather than pretending to verify anything.
 */
export function LedgerDrawer({ runId, entries, head }) {
  const [verify, setVerify] = useState(null);
  const [busy, setBusy] = useState(false);

  async function onVerify() {
    setBusy(true);
    const res = await apiFetch(`/ledger/verify`, { method: "POST", params: { run_id: runId } });
    setVerify(res);
    setBusy(false);
  }

  return (
    <div data-testid="ledger-drawer">
      <div className="mb-2 flex items-center gap-2">
        {!READ_ONLY && (
          <button onClick={onVerify} disabled={!runId || busy} className="rounded bg-gray-800 px-3 py-1 text-xs font-medium text-white disabled:opacity-40">
            {busy ? "Verifying…" : "Verify ledger"}
          </button>
        )}
        {head && <span className="font-mono text-[11px] text-gray-500">head: {head.slice(0, 16)}…</span>}
      </div>
      {!READ_ONLY && verify && (
        verify.ok ? (
          <p className={`text-xs ${verify.data.ok ? "text-emerald-700" : "text-rose-700"}`}>
            {verify.data.ok ? "Chain intact" : `Tampering detected at seq ${verify.data.first_bad_seq}`} — {verify.data.n_entries} entries, head {verify.data.head_hash?.slice(0, 16)}…
          </p>
        ) : (
          <p className="text-xs text-gray-500">
            {verify.kind === "not_implemented" ? "Not available yet: POST /ledger/verify is not implemented on the API." : verify.message}
          </p>
        )
      )}
      <ol className="mt-2 max-h-56 space-y-1 overflow-auto text-[11px] text-gray-600">
        {(entries || []).map((e, i) => (
          <li key={i} className="font-mono">
            #{e.seq} {e.node} → {e.hash?.slice(0, 16)}…
          </li>
        ))}
      </ol>
      {!entries?.length && <p className="text-xs text-gray-400">No ledger entries yet.</p>}
    </div>
  );
}
