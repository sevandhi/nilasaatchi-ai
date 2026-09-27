import { FacetSelect } from "../components/common/FacetSelect.jsx";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DocumentListResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { DocumentModal } from "../components/evidence/DocumentModal.jsx";
import { apiFetch, READ_ONLY } from "../api/client.js";
import { useStore } from "../store/useStore.js";
import { fmtDate } from "../lib/format.js";
import { UploadPanel } from "../components/ingest/UploadPanel.jsx";
import { JobProgress, JobStatusBadge } from "../components/ingest/JobProgress.jsx";
import { IngestJobListResponseSchema } from "../components/ingest/schemas.js";
import { it } from "../components/ingest/labels.js";

const COLUMNS = [
  { key: "id", label: "ID", type: "number" },
  { key: "classified_type", label: "Classified type" },
  { key: "stage", label: "Stage" },
  { key: "village", label: "Village" },
  { key: "doc_date", label: "Date" },
  { key: "pages", label: "Pages", type: "number" },
  { key: "status", label: "Status" },
  { key: "folder_type_mismatch", label: "Folder≠type?", type: "bool" },
];

/** Page 6: documents catalog + a page viewer. */
export function Documents() {
  const [params, setParams] = useSearchParams();
  const lang = useStore((s) => s.lang);
  const [filters, setFilters] = useState({ village: "", classified_type: "", stage: "", q: "" });
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const list = useApi(
    "/documents",
    { schema: DocumentListResponseSchema, params: { ...filters, limit, offset } },
    [filters, offset]
  );

  const facets = useApi("/documents/facets");
  const uploads = useApi(READ_ONLY ? null : "/ingest/jobs", { schema: IngestJobListResponseSchema, params: { kind: "document", limit: 20 } });
  const [expandedJobId, setExpandedJobId] = useState(null);

  const [activeDoc, setActiveDoc] = useState(null);   // {id, page, bbox?} shown in the document popup
  const extractionParam = params.get("extraction");

  // deep link ?extraction=<id>: open the document popup at that value's page, with its evidence box
  useEffect(() => {
    if (!extractionParam) return;
    let live = true;
    apiFetch(`/evidence/${extractionParam}`).then((res) => {
      if (live && res.ok && res.data?.document_id) {
        setActiveDoc({ id: res.data.document_id, page: res.data.page_no || 1, bbox: res.data.bbox || null });
      }
    });
    return () => { live = false; };
  }, [extractionParam]);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Documents{list.data ? ` (${list.data.total})` : ""}</h1>

      {!READ_ONLY && (
        <>
          <UploadPanel
            onJobCreated={() => uploads.reload()}
            onJobSettled={() => {
              uploads.reload();
              list.reload();
              facets.reload();       // a new document can add a new village / type / stage to the dropdowns
            }}
          />

          <section>
            <h2 className="mb-2 text-sm font-semibold text-gray-600">{it("recent_uploads", lang)}</h2>
            <Widget status={uploads.status} error={uploads.error} onRetry={uploads.reload} title={it("recent_uploads", lang)} minHeight="4rem" emptyReason={it("no_recent_uploads", lang)}>
              <div className="overflow-auto rounded-lg border border-gray-200">
                <table className="min-w-full divide-y divide-gray-200 text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium text-gray-600">Job</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600">File</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600">Status</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 bg-white">
                    {(uploads.data?.jobs || []).map((j) => (
                      <tr
                        key={j.id}
                        onClick={() => setExpandedJobId(j.id)}
                        className={`cursor-pointer hover:bg-emerald-50 ${expandedJobId === j.id ? "bg-emerald-100" : ""}`}
                        data-testid="upload-job-row"
                      >
                        <td className="px-3 py-1.5 font-mono text-xs text-gray-500">{j.id}</td>
                        <td className="max-w-xs truncate px-3 py-1.5">{j.filename || "—"}</td>
                        <td className="px-3 py-1.5">
                          <JobStatusBadge status={j.status} />
                        </td>
                        <td className="px-3 py-1.5 text-xs text-gray-500">{j.created_at ? fmtDate(j.created_at) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Widget>
            {expandedJobId && (
              <div className="mt-2">
                <JobProgress jobId={expandedJobId} kind="document" />
              </div>
            )}
          </section>
        </>
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {[["village", "Village"], ["classified_type", "Document type"], ["stage", "Legal stage"]].map(([k, label]) => (
          <FacetSelect key={k} label={label} options={facets.data?.[k]} value={filters[k]}
            onChange={(v) => { setOffset(0); setFilters((f) => ({ ...f, [k]: v })); }} />
        ))}
        <label className="flex items-center gap-1 text-gray-500">
          Search:
          <input
            value={filters.q}
            placeholder="file name, document no. or page text"
            onChange={(e) => { setOffset(0); setFilters((f) => ({ ...f, q: e.target.value })); }}
            className="w-44 rounded border border-gray-300 px-1 py-0.5"
          />
        </label>
        {(filters.village || filters.classified_type || filters.stage || filters.q) && (
          <button onClick={() => { setOffset(0); setFilters({ village: "", classified_type: "", stage: "", q: "" }); }}
            className="rounded border border-gray-300 px-2 py-0.5 text-gray-600">Clear filters</button>
        )}
      </div>

      

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Catalog</h2>
        <Widget status={list.status} error={list.error} onRetry={list.reload} title="Documents" minHeight="8rem">
          <DataTable
            columns={COLUMNS}
            rows={list.data?.items || []}
            onRowClick={(r) => setActiveDoc({ id: r.id, page: 1 })}
            rowKey={(r) => String(r.id)}
            selectedKey={activeDoc ? String(activeDoc.id) : null}
            csvName="documents"
          />
          <div className="mt-2 flex items-center gap-2 text-xs">
            <button disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - limit))} className="rounded border px-2 py-1 disabled:opacity-30">prev</button>
            <span>{offset + 1}–{Math.min(offset + limit, list.data?.total || 0)} of {list.data?.total ?? "—"}</span>
            <button disabled={!list.data || offset + limit >= list.data.total} onClick={() => setOffset((o) => o + limit)} className="rounded border px-2 py-1 disabled:opacity-30">next</button>
          </div>
        </Widget>
      </section>

      {activeDoc && (
        <DocumentModal documentId={activeDoc.id} page={activeDoc.page || 1} bbox={activeDoc.bbox || null}
          title={activeDoc.title} onClose={() => { setActiveDoc(null); if (extractionParam) setParams({}); }} />
      )}
    </div>
  );
}
