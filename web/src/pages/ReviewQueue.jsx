import { useState } from "react";
import { ReviewListResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { ReviewItemCard } from "../components/review/ReviewItemCard.jsx";
import { EvidenceViewer } from "../components/evidence/EvidenceViewer.jsx";

/** Page 9: review queue — uncertain items with a crop, candidate values and the reason. */
export function ReviewQueue() {
  const [reasonFilter, setReasonFilter] = useState("");
  const list = useApi("/review-queue", { schema: ReviewListResponseSchema, params: { status: "open", reason: reasonFilter || undefined, limit: 60 } }, [reasonFilter]);
  const [decided, setDecided] = useState({});
  const [openExtraction, setOpenExtraction] = useState(null);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Review queue{list.data ? ` (${list.data.total} open)` : ""}</h1>
      <label className="text-xs text-gray-500">
        reason filter:{" "}
        <input value={reasonFilter} onChange={(e) => setReasonFilter(e.target.value)} placeholder="e.g. prose_as_table" className="rounded border border-gray-300 px-2 py-1" />
      </label>

      {openExtraction && (
        <div>
          <button onClick={() => setOpenExtraction(null)} className="mb-1 text-xs text-emerald-700 underline">close</button>
          <EvidenceViewer extractionId={openExtraction} />
        </div>
      )}

      <Widget status={list.status} error={list.error} onRetry={list.reload} title="Review queue" minHeight="8rem" emptyReason="Nothing open matches this filter.">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(list.data?.items || []).filter((i) => !decided[i.id]).map((item) => (
            <ReviewItemCard
              key={item.id}
              item={item}
              onOpenExtraction={setOpenExtraction}
              onDecided={(id) => setDecided((d) => ({ ...d, [id]: true }))}
            />
          ))}
        </div>
        {Object.keys(decided).length > 0 && (
          <p className="mt-2 text-xs text-gray-400">{Object.keys(decided).length} item(s) decided this session (hidden above; refresh to confirm against the server).</p>
        )}
      </Widget>
    </div>
  );
}
