import { RetryImg } from "../common/RetryImg.jsx";
import { useEffect, useState } from "react";
import { assetUrl, apiFetch, READ_ONLY } from "../../api/client.js";
import { StatusBadge } from "../common/Badge.jsx";

function pageNoFromRef(pageRef) {
  const m = /#p(\d+)$/.exec(pageRef || "");
  return m ? Number(m[1]) : 1;
}

const REASONS = {
  handwriting: "handwritten page: the AI reading is unreliable",
  blurred_newsprint: "blurred newspaper scan: the AI reading is unreliable",
  self_consistency_fail: "the numbers read from this page do not add up (totals / hectare-acre / amount checks)",
  prose_as_table: "the page is text, not a table: rows may be misread",
  vlm_failed: "the table reader could not read this page",
  owner_sample: "random sample of owner names for quality control",
};
// fields shown in the correction table (owner names are masked and not editable)
const FIELDS = [["survey_no", "Survey"], ["sub_div", "Sub-div"], ["extent_ha", "Extent (ha)"], ["extent_ac", "Extent (ac)"],
  ["amount_rs", "Amount (Rs)"], ["patta_no", "Patta"], ["classification", "Land class"]];

/** One review item = one page the AI was unsure about. The reviewer looks at the page and either
 * approves the values read from it, corrects some of them (the AI's originals are kept for audit),
 * or rejects the page as unreadable. Posts to `POST /review/{id}`. */
export function ReviewItemCard({ item, onDecided, onOpenExtraction }) {
  const [rows, setRows] = useState(null);          // rows the AI read from this page
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});          // {extraction_id: {field: value}}
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let live = true;
    apiFetch(`/review/${item.id}/rows`).then((res) => { if (live) setRows(res.ok ? res.data.rows : []); });
    return () => { live = false; };
  }, [item.id]);

  async function decide(decision) {
    setBusy(true);
    setErr(null);
    const corrections = Object.entries(draft)
      .map(([id, values]) => ({ extraction_id: Number(id), values }))
      .filter((c) => Object.keys(c.values).length);
    if (decision === "corrected" && !corrections.length) {
      setBusy(false);
      setErr("Change at least one value first, or use Approve if the values are right.");
      return;
    }
    const res = await apiFetch(`/review/${item.id}`, { method: "POST", body: { decision, corrections, decided_by: "reviewer@ui" } });
    setBusy(false);
    if (!res.ok) {
      setErr(res.message);
      return;
    }
    setEditing(false);
    onDecided?.(item.id, res.data);
  }

  function setCell(row, field, value) {
    setDraft((d) => {
      const cur = { ...(d[row.extraction_id] || {}) };
      if (String(value) === String(row.values[field] ?? "")) delete cur[field];
      else cur[field] = value;
      return { ...d, [row.extraction_id]: cur };
    });
  }

  const conf = item.detail?.confidence;
  const nRows = rows ? rows.length : null;

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
      <p className="text-xs text-gray-700">
        <b>Why a human is needed:</b> {REASONS[item.reason] || item.reason}
        {typeof conf === "number" && <span className="text-gray-500"> · AI confidence {Math.round(conf * 100)}%</span>}
      </p>
      <p className="mt-0.5 text-[11px] text-gray-500">
        {nRows === null ? "Loading the values read from this page…" : nRows === 0 ? "No table rows were read from this page." : `${nRows} row(s) were read from this page.`}
      </p>
      {item.extraction_id && (
        <button onClick={() => onOpenExtraction?.(item.extraction_id)} className="text-xs text-emerald-700 underline">open full evidence</button>
      )}

      {editing && rows?.length > 0 && (
        <div className="mt-2 max-h-56 overflow-auto rounded border" data-testid="review-editor">
          <table className="w-full text-[11px]">
            <thead className="bg-gray-50"><tr>{FIELDS.map(([, l]) => <th key={l} className="px-1 py-0.5 text-left font-medium">{l}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.extraction_id} className="border-t">
                  {FIELDS.map(([f]) => (
                    <td key={f} className="px-0.5 py-0.5">
                      <input
                        defaultValue={r.values[f] ?? ""}
                        onChange={(e) => setCell(r, f, e.target.value)}
                        className={`w-full rounded border px-1 ${draft[r.extraction_id]?.[f] !== undefined ? "border-sky-500 bg-sky-50" : "border-gray-200"}`}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <p className="mt-1 text-[11px] text-gray-500">
          Type the correct values from the page. Saving marks this page as reviewed: rows you changed become
          <b> corrected</b> (the AI&apos;s values are kept for audit), the other rows <b>approved</b>.
        </p>
      )}
      {err && <p className="mt-1 text-xs text-rose-600">{err}</p>}

      {!READ_ONLY && item.status === "open" && (
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {editing ? (
            <>
              <button disabled={busy} onClick={() => decide("corrected")} className="rounded bg-sky-600 px-2 py-1 font-medium text-white disabled:opacity-40">Save corrections</button>
              <button disabled={busy} onClick={() => { setEditing(false); setDraft({}); setErr(null); }} className="rounded border border-gray-300 px-2 py-1">Cancel</button>
            </>
          ) : (
            <>
              <button disabled={busy || nRows === null} onClick={() => decide("approved")} className="rounded bg-emerald-600 px-2 py-1 font-medium text-white disabled:opacity-40"
                title={nRows ? "The values read from this page are right" : "Confirm the page has no table data to extract"}>
                {nRows ? "Approve values" : "Confirm: nothing to extract"}
              </button>
              {nRows > 0 && (
                <button disabled={busy} onClick={() => setEditing(true)} className="rounded border border-gray-300 px-2 py-1">Correct values</button>
              )}
              <button disabled={busy} onClick={() => decide("rejected")} className="rounded bg-rose-600 px-2 py-1 font-medium text-white disabled:opacity-40"
                title="The page is unreadable or not usable: its rows are excluded">
                Reject page
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
