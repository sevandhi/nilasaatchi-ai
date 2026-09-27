import { fmtNumber } from "../../lib/format.js";

/** Difference-in-differences vs the control group, one row per (event, metric, season scope)
 * (docs/team/07-phase3-satellite-evidence.md §5 — table `parcel_did`). */
export function DidTable({ rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400">No difference-in-differences rows here yet.</p>;
  return (
    <div className="overflow-auto rounded border border-gray-200">
      <table className="min-w-full text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-2 py-1 text-left">event</th>
            <th className="px-2 py-1 text-left">metric</th>
            <th className="px-2 py-1 text-left">scope</th>
            <th className="px-2 py-1 text-right">n pre/post</th>
            <th className="px-2 py-1 text-right">DiD</th>
            <th className="px-2 py-1 text-right">95% CI</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((r, i) => (
            <tr key={i} className={r.did !== null && r.ci_lo !== null && r.ci_hi !== null && (r.ci_lo > 0 || r.ci_hi < 0) ? "bg-amber-50" : ""}>
              <td className="px-2 py-1">{r.event}</td>
              <td className="px-2 py-1">{r.metric}</td>
              <td className="px-2 py-1">{r.season_scope}</td>
              <td className="px-2 py-1 text-right">{r.n_pre ?? "—"}/{r.n_post ?? "—"}</td>
              <td className="px-2 py-1 text-right">{fmtNumber(r.did, 3)}</td>
              <td className="px-2 py-1 text-right">[{fmtNumber(r.ci_lo, 2)}, {fmtNumber(r.ci_hi, 2)}]</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
