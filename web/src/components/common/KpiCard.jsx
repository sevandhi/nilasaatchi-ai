import { VerdictBadge, ConfidencePill } from "./Badge.jsx";
import { fmtNumber } from "../../lib/format.js";

/**
 * @param {{kpi: {id:string,label:string,value:any,unit?:string,claim_id?:string,status?:string,confidence?:number,source?:string}, onClick?: ()=>void}} props
 */
export function KpiCard({ kpi, onClick }) {
  const value = typeof kpi.value === "number" ? fmtNumber(kpi.value) : kpi.value;
  return (
    <button
      onClick={onClick}
      title={kpi.source}
      className="flex flex-col items-start gap-1 rounded-lg border border-gray-200 bg-white p-3 text-left shadow-sm transition hover:border-emerald-400 hover:shadow disabled:cursor-default"
      disabled={!onClick}
    >
      <div className="text-2xl font-semibold text-gray-900">
        {value}
        {kpi.unit ? <span className="ml-1 text-sm font-normal text-gray-500">{kpi.unit}</span> : null}
      </div>
      <div className="text-xs text-gray-500">{kpi.label}</div>
      <div className="mt-1 flex items-center gap-2">
        {kpi.status && <VerdictBadge verdict={kpi.status === "verified" ? "ACCEPT" : kpi.status === "downgraded" ? "DOWNGRADE" : "REVIEW"} />}
        {kpi.confidence !== undefined && <ConfidencePill value={kpi.confidence} />}
      </div>
      {kpi.breakdown && (
        <div className="mt-1 flex flex-wrap gap-1">
          {Object.entries(kpi.breakdown).slice(0, 4).map(([k, v]) => (
            <span key={k} className="rounded bg-gray-100 px-1 text-[10px] text-gray-500">{k}: {v ?? "—"}</span>
          ))}
          {Object.keys(kpi.breakdown).length > 4 && <span className="text-[10px] text-gray-400">+{Object.keys(kpi.breakdown).length - 4} more</span>}
        </div>
      )}
      {kpi.source && /\bselect\b/i.test(kpi.source) && <div className="mt-1 truncate text-[10px] text-gray-400" title={kpi.source}>SQL: {kpi.source}</div>}
    </button>
  );
}
