import { RetryImg } from "../common/RetryImg.jsx";
import { useState } from "react";
import { assetUrl, apiFetch, READ_ONLY } from "../../api/client.js";
import { StatusBadge } from "../common/Badge.jsx";

function pageNoFromRef(pageRef) {
  const m = /#p(\d+)$/.exec(pageRef || "");
  return m ? Number(m[1]) : 1;
}

/** One review-queue item: crop, candidate value(s), reason, accept/edit/reject
 * (phase6 SKILL: ReviewQueue). Posts to `POST /review/{id}`. */
export function ReviewItemCard({ item, onDecided, onOpenExtraction }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(JSON.stringify(item.detail?.candidate_value ?? item.detail ?? {}, null, 2));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function decide(decision, corrected_value = null) {
    setBusy(true);
    setErr(null);
    const res = await apiFetch(`/review/${item.id}`, { method: "POST", body: { decision, corrected_value, decided_by: "reviewer@ui" } });
    setBusy(false);
    if (!res.ok) {
      setErr(res.message);
      return;
    }
    onDecided?.(item.id, res.data);
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 text-sm" data-testid="review-item">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-xs text-gray-400">#{item.id}</span>
        <StatusBadge status={item.status} />
      </div>
      {item.document_id ? (
        <RetryImg src={assetUrl(`/documents/${item.document_id}/pages/${pageNoFromRef(item.page_ref)}.webp`)} alt="page crop" className="mb-2 h-32 w-full rounded border object-cover" />
      ) : (
        <div className="mb-2 flex h-32 items-center justify-center rounded border border-dashed text-xs text-gray-400">no page image</div>
      )}
      <p className="truncate text-[10px] text-gray-400" title={item.page_ref}>{item.page_ref}</p>
      <p className="text-xs text-gray-500">reason: <span className="text-gray-700">{item.reason}</span></p>
      {item.extraction_id && (
        <button onClick={() => onOpenExtraction?.(item.extraction_id)} className="text-xs text-emerald-700 underline">open full evidence</button>
      )}
      {!READ_ONLY && editing ? (
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} className="mt-2 h-24 w-full rounded border p-1 font-mono text-[11px]" />
      ) : (
        <pre className="mt-2 max-h-24 overflow-auto rounded bg-gray-50 p-1 text-[11px]">{JSON.stringify(item.detail, null, 2)}</pre>
      )}
      {err && <p className="mt-1 text-xs text-rose-600">{err}</p>}
      {!READ_ONLY && (
        <div className="mt-2 flex gap-2 text-xs">
          <button disabled={busy} onClick={() => decide("approved")} className="rounded bg-emerald-600 px-2 py-1 font-medium text-white disabled:opacity-40">Accept</button>
          {editing ? (
            <button
              disabled={busy}
              onClick={() => {
                try {
                  decide("corrected", JSON.parse(draft));
                  setEditing(false);
                } catch {
                  setErr("draft is not valid JSON");
                }
              }}
              className="rounded bg-sky-600 px-2 py-1 font-medium text-white disabled:opacity-40"
            >
              Save edit
            </button>
          ) : (
            <button disabled={busy} onClick={() => setEditing(true)} className="rounded border border-gray-300 px-2 py-1">Edit</button>
          )}
          <button disabled={busy} onClick={() => decide("rejected")} className="rounded bg-rose-600 px-2 py-1 font-medium text-white disabled:opacity-40">Reject</button>
        </div>
      )}
    </div>
  );
}
