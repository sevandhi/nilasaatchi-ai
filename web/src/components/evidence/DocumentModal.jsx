import { useEffect, useState } from "react";
import { assetUrl } from "../../api/client.js";
import { useApi } from "../../hooks/useApi.js";
import { RetryImg } from "../common/RetryImg.jsx";

/**
 * Document viewer in a modal: the scanned page, previous / next page, and (optionally) a box around the
 * value the evidence points to on its page. Close with the button, Esc, or a click outside.
 * Used by the evidence pack (paper evidence) and the review queue.
 */
export function DocumentModal({ documentId, page = 1, bbox = null, title, onClose }) {
  const meta = useApi(documentId ? `/documents/${documentId}/meta` : null, {}, [documentId]);
  const [cur, setCur] = useState(page || 1);
  const pages = meta.data?.pages || null;
  const boxPage = page || 1;

  useEffect(() => setCur(page || 1), [documentId, page]);
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose?.();
      if (e.key === "ArrowRight") setCur((c) => (pages ? Math.min(pages, c + 1) : c + 1));
      if (e.key === "ArrowLeft") setCur((c) => Math.max(1, c - 1));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pages, onClose]);

  if (!documentId) return null;
  const m = meta.data || {};
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose} data-testid="document-modal">
      <div className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b px-4 py-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-gray-900">{title || m.file_name || `Document ${documentId}`}</div>
            <div className="truncate text-xs text-gray-500">
              {[m.classified_type, m.stage, m.village, m.folder_label].filter(Boolean).join(" · ")}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 text-sm">
            <button onClick={() => setCur((c) => Math.max(1, c - 1))} disabled={cur <= 1}
              className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40" data-testid="doc-prev">‹ Prev</button>
            <span className="w-24 text-center text-xs text-gray-600" data-testid="doc-page">page {cur}{pages ? ` of ${pages}` : ""}</span>
            <button onClick={() => setCur((c) => (pages ? Math.min(pages, c + 1) : c + 1))} disabled={pages ? cur >= pages : false}
              className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40" data-testid="doc-next">Next ›</button>
            <button onClick={onClose} className="ml-2 rounded bg-gray-800 px-3 py-1 font-medium text-white" data-testid="doc-close">Close</button>
          </div>
        </div>
        <div className="overflow-auto bg-gray-100 p-3">
          <div className="relative mx-auto w-fit">
            <RetryImg src={assetUrl(`/documents/${documentId}/pages/${cur}.webp`)} alt={`page ${cur}`} className="block max-h-[78vh] w-auto" />
            {bbox && cur === boxPage && (
              <div
                className="pointer-events-none absolute border-2 border-rose-500 bg-rose-500/10"
                style={{ left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${(bbox[2] - bbox[0]) * 100}%`, height: `${(bbox[3] - bbox[1]) * 100}%` }}
              />
            )}
          </div>
        </div>
        {bbox && <div className="border-t px-4 py-1.5 text-[11px] text-gray-500">The red box on page {boxPage} marks the rows the evidence comes from.</div>}
      </div>
    </div>
  );
}
