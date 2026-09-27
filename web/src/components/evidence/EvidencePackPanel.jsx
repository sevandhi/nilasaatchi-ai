import { RetryImg } from "../common/RetryImg.jsx";
import { useApi } from "../../hooks/useApi.js";
import { Widget } from "../common/Widget.jsx";
import { VerdictBadge, ConfidencePill, StatusBadge } from "../common/Badge.jsx";
import { assetUrl } from "../../api/client.js";
import { DidTable } from "../parcel/DidTable.jsx";
import { chipDates } from "../../lib/chips.js";
import { useStore } from "../../store/useStore.js";
import { maskDeep } from "../../lib/mask.js";

// Evidence rows come from several rules: `document` is either a string or {path, folder_label}.
// Show "folder · file name" (no full paths in the UI) and never pass an object to React as a child.
function docLabel(d) {
  if (!d) return "";
  if (typeof d === "string") return d.split("/").pop();
  const file = typeof d.path === "string" ? d.path.split("/").pop() : "";
  return [d.folder_label, file].filter(Boolean).join(" · ");
}
function textOf(v) {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/**
 * `EvidencePack` renderer: paper crop refs + fields, chips grid, DiD numbers, verdict,
 * confidence, caveats, exportable as JSON (phase6 SKILL: EvidencePackPanel; ui-spec §4/§5).
 * Reads `GET /findings/{id}/evidence-pack` (real shape: `pipeline.findings.evidence_pack`).
 */
/** "In simple words": the finding explained for non-experts (AI via the router, cached; template fallback). */
function PlainSummary({ findingId }) {
  const lang = useStore((s) => s.lang);
  const { status, data } = useApi(`/findings/${findingId}/plain-summary`, { params: { lang } }, [findingId, lang]);
  if (status === "error" || status === "empty") return null;      // e.g. the read-only cloud demo
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3" data-testid="plain-summary">
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-emerald-800">In simple words</div>
      {status !== "ok" || !data ? (
        <p className="text-xs text-gray-500">Writing a simple explanation…</p>
      ) : (
        <div className="space-y-1.5 text-[13px] leading-snug text-gray-800">
          <p>{data.summary}</p>
          <p><b>Why it matters:</b> {data.what_it_means}</p>
          <p><b>How sure are we:</b> {data.how_sure}</p>
          <p><b>What to check next:</b> {data.what_next}</p>
          <p className="text-[10px] text-gray-500">
            {data.source === "template" ? "Standard explanation." : "Written by AI from the evidence below."} The facts and numbers below are the source.
          </p>
        </div>
      )}
    </div>
  );
}

export function EvidencePackPanel({ findingId, onOpenExtraction }) {
  const demoMask = useStore((s) => s.demoMask);
  const { status, data, error, reload } = useApi(findingId ? `/findings/${findingId}/evidence-pack` : null, {}, [findingId]);
  if (!findingId) return <Widget status="empty" emptyReason="Select a finding to open its evidence pack." />;

  const pack = data?.pack;

  function exportJson() {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `evidence-pack-${findingId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const planet = pack?.planet || {};
  const chips = chipDates(planet.chips?.cached);
  const parcelUid = pack?.parcel_uid;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3" data-testid="evidence-pack">
      <Widget status={status} error={error} onRetry={reload} minHeight="16rem">
        {pack && (
          <div className="space-y-3 text-sm">
            <PlainSummary findingId={findingId} />
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-medium text-gray-800">{pack.title}</div>
                <div className="mt-0.5 text-xs text-gray-500">{pack.category} · {pack.severity} · parcel {parcelUid}</div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {pack.verdict && <VerdictBadge verdict={pack.verdict} />}
                <ConfidencePill value={pack.confidence} />
                <button onClick={exportJson} className="rounded border border-gray-300 px-2 py-1 text-xs">Export JSON</button>
              </div>
            </div>

            {pack.metrics && (
              <pre className="max-h-24 overflow-auto rounded bg-gray-50 p-2 text-[11px] text-gray-600">{JSON.stringify(pack.metrics, null, 2)}</pre>
            )}

            <section>
              <h4 className="mb-1 text-xs font-semibold uppercase text-gray-500">Paper (document evidence)</h4>
              <ul className="max-h-64 space-y-1 overflow-auto">
                {(pack.paper || []).map((p, i) => (
                  <li key={i} className="rounded border border-gray-200 p-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{textOf(p.doc_type || p.stage) || "event"} · {textOf(p.date || p.event_date) || "—"}</span>
                      {p.extraction_id && (
                        <button onClick={() => onOpenExtraction?.(p.extraction_id)} className="text-emerald-700 underline">open evidence</button>
                      )}
                    </div>
                    <div className="mt-0.5 text-gray-500">{docLabel(p.document) || textOf(p.note)}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-gray-400">
                      {p.confidence !== undefined && <ConfidencePill value={p.confidence} />}
                      {p.review_status && <StatusBadge status={p.review_status} />}
                    </div>
                    {p.values && <pre className="mt-1 max-h-24 overflow-auto rounded bg-gray-50 p-1 text-[10px]">{JSON.stringify(maskDeep(p.values, demoMask), null, 2)}</pre>}
                  </li>
                ))}
                {!pack.paper?.length && <p className="text-gray-400">No paper-side evidence rows recorded.</p>}
              </ul>
            </section>

            <section>
              <h4 className="mb-1 text-xs font-semibold uppercase text-gray-500">Planet (DiD vs controls)</h4>
              <DidTable rows={planet.parcel_did} />
            </section>

            {chips.length ? (
              <section>
                <h4 className="mb-1 text-xs font-semibold uppercase text-gray-500">Chips</h4>
                <div className="flex flex-wrap gap-2">
                  {chips.map((c) => (
                    <figure key={c.date} className="text-center text-[10px] text-gray-500">
                      <div className="flex gap-1">
                        {c.kinds.includes("truecolor") && <RetryImg src={assetUrl(`/chips/${encodeURIComponent(parcelUid)}/${c.date}.png?kind=truecolor`)} alt={c.date} className="h-16 w-16 rounded border object-cover" />}
                        {c.kinds.includes("ndvi") && <RetryImg src={assetUrl(`/chips/${encodeURIComponent(parcelUid)}/${c.date}.png?kind=ndvi`)} alt={c.date} className="h-16 w-16 rounded border object-cover" />}
                      </div>
                      <figcaption>{c.date}</figcaption>
                    </figure>
                  ))}
                </div>
              </section>
            ) : null}

            <section>
              <h4 className="mb-1 text-xs font-semibold uppercase text-gray-500">Caveats</h4>
              <ul className="list-disc pl-4 text-xs text-amber-800">
                {(pack.caveats || []).map((c, i) => <li key={i}>{c}</li>)}
                {!pack.caveats?.length && <li className="list-none text-gray-400">none recorded</li>}
              </ul>
            </section>
            <p className="text-[11px] text-gray-400">rule version: {pack.rule_version || "—"} · status: {pack.status}{data.source ? ` · source: ${data.source}` : ""}</p>
          </div>
        )}
      </Widget>
    </div>
  );
}
