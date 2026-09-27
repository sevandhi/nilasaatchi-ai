import { useNavigate } from "react-router-dom";
import { StatsOverviewResponseSchema } from "../api/schemas.js";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { KpiCard } from "../components/common/KpiCard.jsx";
import { SatelliteFreshness } from "../components/ingest/SatelliteFreshness.jsx";
import { useStore } from "../store/useStore.js";

const PIPELINE = [
  { id: "sources", label: "Sources", to: "/documents", desc: "Scanned PDFs across stage folders, plus FMB parcel maps." },
  { id: "paper", label: "Paper", to: "/documents", desc: "OCR + extraction with page/box evidence, linked to parcels." },
  { id: "planet", label: "Planet", to: "/parcel", desc: "Per-parcel Sentinel-2 time series 2019→now, seasonal land-use, control-group DiD." },
  { id: "kg", label: "Knowledge graph", to: "/map", desc: "Documents, parcels, facts, events and satellite observations joined on parcel_uid." },
  { id: "agent", label: "Agent", to: "/agent", desc: "Plans, routes across free models, verifies, critiques and judges every claim." },
  { id: "findings", label: "Findings", to: "/findings", desc: "Paper-vs-planet discrepancies with evidence, confidence and caveats." },
];

/** Page 1: "What this system is" — plain explanation + clickable pipeline + KPI tiles. */
export function Overview() {
  const navigate = useNavigate();
  const lang = useStore((s) => s.lang);
  const stats = useApi("/stats/overview", { schema: StatsOverviewResponseSchema });

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-6">
      <section>
        <h1 className="text-xl font-semibold text-gray-900">NilaSaatchi AI — Paper vs Planet</h1>
        <p className="mt-2 text-sm leading-relaxed text-gray-700">
          Tamil Nadu acquires land for the Allikulam SIPCOT industrial park through about ten legal stages, each
          producing documents that make claims about specific parcels — land class, extent, owners, trees and
          possession dates. Those claims are locked in scanned paper and are rarely checked against what actually
          happens on the ground. This system reads the documents with evidence (page and bounding box for every
          value), builds each parcel&rsquo;s Sentinel-2 satellite history from 2019 to now, and uses an agent that plans,
          routes across free AI models, verifies and critiques its own claims before presenting them — always with
          confidence, evidence and caveats attached. It does not decide what happened; it surfaces measured
          disagreements between paper and planet for a human to check.
        </p>
        {lang === "ta" && (
          <p className="mt-2 text-xs text-gray-400">(The agent answers in the language you ask in — this overview page&rsquo;s chrome is label-translated only.)</p>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Pipeline</h2>
        <div className="flex flex-wrap gap-2">
          {PIPELINE.map((box, i) => (
            <div key={box.id} className="flex items-center">
              <button
                onClick={() => navigate(box.to)}
                className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-left text-xs shadow-sm hover:border-emerald-500 hover:bg-emerald-50"
              >
                <div className="font-semibold text-gray-800">{box.label}</div>
                <div className="mt-0.5 max-w-[11rem] text-gray-500">{box.desc}</div>
              </button>
              {i < PIPELINE.length - 1 && <span className="mx-1 text-gray-300">→</span>}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Key measured numbers</h2>
        <Widget status={stats.status} error={stats.error} onRetry={stats.reload} title="KPIs" minHeight="6rem">
          {stats.data && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {stats.data.kpis.map((k) => <KpiCard key={k.id} kpi={k} />)}
            </div>
          )}
        </Widget>
      </section>

      <section>
        <SatelliteFreshness />
      </section>

    </div>
  );
}
