import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { DataTable } from "../components/common/DataTable.jsx";
import { ChartWidget } from "../components/charts/ChartWidget.jsx";
import { EvidencePackPanel } from "../components/evidence/EvidencePackPanel.jsx";
import { EvidenceViewer } from "../components/evidence/EvidenceViewer.jsx";
import { useStore } from "../store/useStore.js";

const COLUMNS = [
  { key: "id", label: "ID", type: "number" },
  { key: "parcel_uid", label: "Parcel" },
  { key: "category", label: "Category" },
  { key: "severity", label: "Severity" },
  { key: "confidence", label: "Confidence", type: "number" },
  { key: "evidence_level", label: "Evidence level" },
  { key: "village", label: "Village" },
  { key: "status", label: "Status" },
];

/** Page 4: findings table + summary chart + idle-land bank + evidence pack view. */
export function Findings() {
  const [params, setParams] = useSearchParams();
  const { filters, setFilter, selection, selectFinding } = useStore((s) => s);
  const evidenceId = params.get("evidence") || selection.findingId;
  const [openExtractionId, setOpenExtractionId] = useState(null);

  const list = useApi(
    "/findings",
    { params: { category: filters.category, severity: filters.severity, village: filters.village, block: filters.block, level: filters.level, limit: 200 } },
    [filters]
  );
  const summary = useApi("/findings/summary");
  const idleLand = useApi("/idle-land");
  const facets = useApi("/findings/facets");
  const evidenceRef = useRef(null);

  // the evidence pack opens at the top of the page: bring it into view whenever a finding is opened
  useEffect(() => {
    if (evidenceId) evidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [evidenceId]);

  function closeEvidence() {
    selectFinding(null);
    setParams({});
    setOpenExtractionId(null);
  }

  const rows = list.data?.items || [];

  const chartSpec = useMemo(() => {
    const items = summary.data?.items || [];
    if (!items.length) return null;
    const categories = [...new Set(items.map((i) => i.category))];
    const severities = [...new Set(items.map((i) => i.severity))];
    // `v_finding_summary` has one row per (category, severity, evidence_level) — sum across
    // evidence_level so each category/severity cell reflects every finding, not just one level.
    const data = categories.map((cat) => {
      const row = { category: cat };
      for (const sev of severities) {
        row[sev] = items.filter((i) => i.category === cat && i.severity === sev).reduce((sum, i) => sum + i.n, 0);
      }
      return row;
    });
    return { id: "findings-summary", title: "Findings by category × severity", type: "stacked_bar", x: "category", y: severities, data };
  }, [summary.data]);

  function openEvidence(row) {
    selectFinding(row.id);
    setParams({ evidence: String(row.id) });
    setOpenExtractionId(null);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-gray-900">Findings{list.data ? ` (${list.data.total} open)` : ""}</h1>

      {evidenceId && (
        <section ref={evidenceRef} className="mx-auto w-full max-w-4xl scroll-mt-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-600">Evidence pack · finding {evidenceId}</h2>
            <button onClick={closeEvidence} className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600">Close</button>
          </div>
          <div className="space-y-3">
            <EvidencePackPanel findingId={evidenceId} onOpenExtraction={setOpenExtractionId} />
            {openExtractionId && <EvidenceViewer extractionId={openExtractionId} />}
          </div>
        </section>
      )}

      <div className="flex flex-wrap gap-2 text-xs">
        <Filter label="Category" options={facets.data?.category} labels={CATEGORY_LABELS} value={filters.category} onChange={(v) => setFilter("category", v)} />
        <Filter label="Severity" options={facets.data?.severity} value={filters.severity} onChange={(v) => setFilter("severity", v)} />
        <Filter label="Village" options={facets.data?.village} value={filters.village} onChange={(v) => setFilter("village", v)} />
        <Filter label="Block" options={facets.data?.block} value={filters.block} onChange={(v) => setFilter("block", v)} />
        <Filter label="Evidence level" options={facets.data?.level} value={filters.level} onChange={(v) => setFilter("level", v)} />
        {(filters.category || filters.severity || filters.village || filters.block || filters.level) && (
          <button onClick={() => ["category", "severity", "village", "block", "level"].forEach((k) => setFilter(k, null))} className="rounded border border-gray-300 px-2 py-0.5 text-gray-600">Clear filters</button>
        )}
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">By category × severity</h2>
        <Widget status={summary.status} error={summary.error} onRetry={summary.reload} title="Findings summary" minHeight="6rem" emptyReason="No findings summary rows.">
          {chartSpec && <ChartWidget spec={chartSpec} />}
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">All findings</h2>
        <Widget status={list.status} error={list.error} onRetry={list.reload} title="Findings" minHeight="8rem">
          <DataTable columns={COLUMNS} rows={rows} onRowClick={openEvidence} rowKey={(r) => String(r.id)} selectedKey={evidenceId ? String(evidenceId) : null} csvName="findings" />
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Idle land bank by block (ha, road/substation distance)</h2>
        <Widget status={idleLand.status} error={idleLand.error} onRetry={idleLand.reload} title="Idle land" minHeight="6rem" emptyReason="No idle-land bank rows.">
          {idleLand.data && (
            <DataTable
              columns={[
                { key: "village", label: "Village" },
                { key: "block_id", label: "Block", type: "number" },
                { key: "idle_ha", label: "Idle (ha)", type: "number" },
                { key: "n_parcels", label: "Parcels", type: "number" },
                { key: "oldest_possession", label: "Oldest possession" },
                { key: "min_major_road_km", label: "Dist. major road (km)", type: "number" },
                { key: "min_substation_km", label: "Dist. substation (km)", type: "number" },
                { key: "confidence", label: "Confidence", type: "number" },
              ]}
              rows={idleLand.data.items}
              rowKey={(r) => `${r.village}-${r.block_id}`}
              csvName="idle-land"
            />
          )}
        </Widget>
      </section>


    </div>
  );
}

const CATEGORY_LABELS = {
  EXTENT_MISMATCH: "Extent mismatch", PV3_POST_POSSESSION_ACTIVITY: "Change after possession", FMB_QUALITY: "Map quality (FMB)",
  COMPENSATION_MISMATCH: "Compensation mismatch", PV4_IDLE_LAND_BANK: "Idle land", EXTRACTION_ERROR: "Extraction error",
  PV1_CLASSIFICATION_CONFLICT: "Land-class conflict", DOC_VERSION_CONFLICT: "Proposal vs sanction",
};

function Filter({ label, options, labels = {}, value, onChange }) {
  return (
    <label className="flex items-center gap-1 text-gray-500">
      {label}:
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} className="max-w-[14rem] rounded border border-gray-300 px-1 py-0.5 text-gray-800" data-testid={`filter-${label.toLowerCase().replace(/\s+/g, "-")}`}>
        <option value="">All</option>
        {(options || []).map((o) => (
          <option key={o.value} value={o.value}>{labels[o.value] || o.value} ({o.count})</option>
        ))}
      </select>
    </label>
  );
}
