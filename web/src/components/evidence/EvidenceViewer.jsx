import { RetryImg } from "../common/RetryImg.jsx";
import { useRef, useState } from "react";
import { EvidenceResponseSchema } from "../../api/schemas.js";
import { useApi } from "../../hooks/useApi.js";
import { assetUrl } from "../../api/client.js";
import { Widget } from "../common/Widget.jsx";
import { ConfidencePill, PrivacyTierBadge, StatusBadge } from "../common/Badge.jsx";
import { useStore } from "../../store/useStore.js";
import { maskDeep } from "../../lib/mask.js";

/**
 * Page image (webp) with the extraction's bounding box drawn over it, plus the raw
 * row/engine/confidence/verification detail (phase6 SKILL: EvidenceViewer).
 * @param {{extractionId: number|null, extractionIds?: number[], onNavigate?: (id:number)=>void}} props
 */
export function EvidenceViewer({ extractionId, extractionIds, onNavigate }) {
  const demoMask = useStore((s) => s.demoMask);
  const { status, data, error, reload } = useApi(
    extractionId ? `/evidence/${extractionId}` : null,
    { schema: EvidenceResponseSchema },
    [extractionId]
  );
  const imgRef = useRef(null);
  const [imgSize, setImgSize] = useState(null);

  if (!extractionId) return <Widget status="empty" emptyReason="No extraction selected yet — click a document row or a bbox reference." />;

  const idx = extractionIds ? extractionIds.indexOf(extractionId) : -1;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      {extractionIds && extractionIds.length > 1 && (
        <div className="mb-2 flex items-center justify-between text-xs">
          <button
            disabled={idx <= 0}
            onClick={() => onNavigate?.(extractionIds[idx - 1])}
            className="rounded border px-2 py-0.5 disabled:opacity-30"
          >
            ◀ prev
          </button>
          <span>{idx + 1} / {extractionIds.length}</span>
          <button
            disabled={idx < 0 || idx >= extractionIds.length - 1}
            onClick={() => onNavigate?.(extractionIds[idx + 1])}
            className="rounded border px-2 py-0.5 disabled:opacity-30"
          >
            next ▶
          </button>
        </div>
      )}
      <Widget status={status} error={error} onRetry={reload} minHeight="20rem">
        {data && (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="relative">
              <RetryImg
                ref={imgRef}
                src={assetUrl(`/documents/${data.document_id}/pages/${data.page_no}.webp`)}
                alt={`page ${data.page_no} of document ${data.document_id}`}
                onLoad={(e) => setImgSize({ w: e.target.clientWidth, h: e.target.clientHeight })}
                className="w-full rounded border"
              />
              {data.bbox && imgSize && (
                <div
                  className="pointer-events-none absolute border-2 border-rose-500"
                  style={{
                    left: data.bbox[0] * imgSize.w,
                    top: data.bbox[1] * imgSize.h,
                    width: (data.bbox[2] - data.bbox[0]) * imgSize.w,
                    height: (data.bbox[3] - data.bbox[1]) * imgSize.h,
                  }}
                  data-testid="evidence-bbox"
                />
              )}
              <p className="mt-1 text-[11px] text-gray-400">{data.page_ref}</p>
            </div>
            <div className="text-sm">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <StatusBadge status={data.review_status} />
                <PrivacyTierBadge tier={data.privacy_tier} />
                <ConfidencePill value={data.confidence} />
                {data.masked && <span className="rounded bg-gray-100 px-2 py-0.5 text-[11px] text-gray-500">owner masked</span>}
              </div>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-xs text-gray-600">
                <dt className="text-gray-400">schema</dt>
                <dd>{data.schema_name}</dd>
                <dt className="text-gray-400">doc type</dt>
                <dd>{data.doc_type || "—"}</dd>
                <dt className="text-gray-400">extractor / engine</dt>
                <dd>{data.extractor || "—"}</dd>
                <dt className="text-gray-400">bbox method</dt>
                <dd>{data.bbox_method || "—"}</dd>
              </dl>
              <div className="mt-2">
                <div className="mb-1 text-xs font-medium text-gray-500">Extracted row (raw)</div>
                <pre className="max-h-56 overflow-auto rounded bg-gray-50 p-2 text-[11px] text-gray-700">
                  {JSON.stringify(maskDeep(data.row, demoMask), null, 2)}
                </pre>
              </div>
            </div>
          </div>
        )}
      </Widget>
    </div>
  );
}
