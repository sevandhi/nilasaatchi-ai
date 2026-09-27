import { RetryImg } from "../components/common/RetryImg.jsx";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DocumentListResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { EvidenceViewer } from "../components/evidence/EvidenceViewer.jsx";
import { assetUrl, READ_ONLY } from "../api/client.js";
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

  const uploads = useApi(READ_ONLY ? null : "/ingest/jobs", { schema: IngestJobListResponseSchema, params: { kind: "document", limit: 20 } });
  const [expandedJobId, setExpandedJobId] = useState(null);

  const [activeDoc, setActiveDoc] = useState(null);
  const [page, setPage] = useState(1);
  const extractionParam = params.get("extraction");

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

      <div className="flex flex-wrap gap-2 text-xs">
        {["village", "classified_type", "stage", "q"].map((k) => (
          <label key={k} className="flex items-center gap-1 text-gray-500">
            {k}:
            <input
              value={filters[k]}
              onChange={(e) => { setOffset(0); setFilters((f) => ({ ...f, [k]: e.target.value })); }}
              className="w-32 rounded border border-gray-300 px-1 py-0.5"
            />
          </label>
        ))}
      </div>

      

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Catalog</h2>
        <Widget status={list.status} error={list.error} onRetry={list.reload} title="Documents" minHeight="8rem">
          <DataTable
            columns={COLUMNS}
            rows={list.data?.items || []}
            onRowClick={(r) => { setActiveDoc(r); setPage(1); }}
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

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Page viewer</h2>
        {extractionParam ? (
          <div>
            <button onClick={() => setParams({})} className="mb-2 text-xs text-emerald-700 underline">close deep-linked evidence</button>
            <EvidenceViewer extractionId={Number(extractionParam)} />
          </div>
        ) : activeDoc ? (
          <div className="rounded-lg border border-gray-200 bg-white p-3">
            <div className="mb-2 flex items-center gap-2 text-xs">
              <span className="font-mono text-gray-500">doc {activeDoc.id}</span>
              <span className="truncate text-gray-400">{activeDoc.path}</span>
              <div className="ml-auto flex items-center gap-1">
                <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="rounded border px-1.5 disabled:opacity-30">◀</button>
                <span>page {page}/{activeDoc.pages}</span>
                <button disabled={page >= activeDoc.pages} onClick={() => setPage((p) => p + 1)} className="rounded border px-1.5 disabled:opacity-30">▶</button>
              </div>
            </div>
            <RetryImg src={assetUrl(`/documents/${activeDoc.id}/pages/${page}.webp`)} alt={`page ${page}`} className="max-h-[32rem] w-auto rounded border" />
            <p className="mt-1 text-[11px] text-gray-400">
              Bounding-box overlays need a specific extraction id — open a page from a parcel&rsquo;s Documents section, or
              append <code>?extraction=&lt;id&gt;</code> to this page&rsquo;s URL.
            </p>
          </div>
        ) : (
          <p className="text-xs text-gray-400">Click a row in the catalog to preview its pages.</p>
        )}
      </section>
    </div>
  );
}
