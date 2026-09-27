import { RetryImg } from "../components/common/RetryImg.jsx";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useApi } from "../hooks/useApi.js";
import { Widget } from "../components/common/Widget.jsx";
import { PaperPlanetTimeline } from "../components/parcel/PaperPlanetTimeline.jsx";
import { EvidenceViewer } from "../components/evidence/EvidenceViewer.jsx";
import { DidTable } from "../components/parcel/DidTable.jsx";
import { StatusBadge, ConfidencePill } from "../components/common/Badge.jsx";
import { assetUrl } from "../api/client.js";
import { useStore } from "../store/useStore.js";
import { fmtNumber } from "../lib/format.js";
import { chipDates } from "../lib/chips.js";

/** Page 3 (the signature view): header, Paper-vs-Planet timeline, documents, satellite, findings. */
export function ParcelPage() {
  const { uid: uidParam } = useParams();
  const navigate = useNavigate();
  const { selection, selectParcel, selectExtraction } = useStore((s) => s);
  const uid = uidParam || selection.parcelUid;

  useEffect(() => {
    if (uidParam && uidParam !== selection.parcelUid) selectParcel(uidParam);
  }, [uidParam]); // eslint-disable-line react-hooks/exhaustive-deps

  const header = useApi(uid ? `/parcels/${encodeURIComponent(uid)}` : null, {}, [uid]);
  const extractions = useApi(uid ? `/parcels/${encodeURIComponent(uid)}/extractions` : null, {}, [uid]);
  const satellite = useApi(uid ? `/parcels/${encodeURIComponent(uid)}/satellite` : null, {}, [uid]);
  const findings = useApi(uid ? `/parcels/${encodeURIComponent(uid)}/findings` : null, { isEmpty: (d) => Array.isArray(d) && d.length === 0 }, [uid]);

  const [activeExtraction, setActiveExtraction] = useState(null);
  const extractionRows = useMemo(() => extractions.data?.items || [], [extractions.data]);
  const extractionIds = useMemo(() => extractionRows.map((r) => r.extraction_id), [extractionRows]);
  const chips = useMemo(() => chipDates(satellite.data?.chips), [satellite.data]);
  const didRows = satellite.data?.did || [];

  if (!uid) {
    return (
      <div>
        <h1 className="text-lg font-semibold">Parcel</h1>
        <p className="mt-2 text-sm text-gray-500">No parcel selected. Pick one from the <button className="text-emerald-700 underline" onClick={() => navigate("/map")}>map</button>.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section>
        <div className="flex items-center justify-between">
          <h1 className="font-mono text-lg font-semibold text-gray-900">{uid}</h1>
          <button onClick={() => navigate("/map")} className="text-xs text-emerald-700 underline">back to map</button>
        </div>
        <Widget status={header.status} error={header.error} onRetry={header.reload} title="Parcel header" minHeight="4rem">
          {header.data && (
            <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-gray-700 sm:grid-cols-4">
              <dt className="text-gray-400">village/unit/block</dt><dd>{header.data.village}/{header.data.unit_id ?? "—"}/{header.data.block_id ?? "—"}</dd>
              <dt className="text-gray-400">geodesic area (GIS / UTM)</dt><dd>{fmtNumber(header.data.area_ha_gis, 4)} / {fmtNumber(header.data.area_ha_utm, 4)} ha</dd>
              <dt className="text-gray-400">stage (days in stage)</dt><dd>{header.data.current_stage || "—"} {header.data.days_in_stage != null && `(${header.data.days_in_stage}d)`}</dd>
              <dt className="text-gray-400">evidence level</dt><dd>{header.data.evidence_level || "—"}</dd>
              <dt className="text-gray-400">open findings</dt><dd>{header.data.n_findings ?? "—"}</dd>
              <dt className="text-gray-400">inside park boundary</dt><dd>{header.data.inside_park_boundary ? "yes" : "no"}</dd>
            </dl>
          )}
        </Widget>
      </section>

      <PaperPlanetTimeline
        parcelUid={uid}
        onEventClick={(evt) => { if (evt?.extraction_id) { setActiveExtraction(evt.extraction_id); selectExtraction(evt.extraction_id); } }}
      />

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Documents (linked extractions)</h2>
        <Widget status={extractions.status} error={extractions.error} onRetry={extractions.reload} title="Extractions" minHeight="6rem" emptyReason="No extraction is linked to this parcel yet.">
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ul className="max-h-80 space-y-1 overflow-auto text-xs">
              {extractionRows.map((r) => (
                <li key={r.extraction_id}>
                  <button
                    onClick={() => { setActiveExtraction(r.extraction_id); selectExtraction(r.extraction_id); }}
                    className={`w-full rounded border p-1.5 text-left ${activeExtraction === r.extraction_id ? "border-emerald-400 bg-emerald-50" : "border-gray-200"}`}
                  >
                    <div className="flex items-center justify-between">
                      <span>{r.fact_type || `extraction ${r.extraction_id}`} · doc {r.document_id}</span>
                      <StatusBadge status={r.review_status} />
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-gray-400">
                      <ConfidencePill value={r.confidence} />
                      <span>self-consistency: {r.self_consistency || "—"}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
            <EvidenceViewer extractionId={activeExtraction} extractionIds={extractionIds} onNavigate={(id) => { setActiveExtraction(id); selectExtraction(id); }} />
          </div>
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Satellite: chips + DiD vs controls</h2>
        <Widget status={satellite.status} error={satellite.error} onRetry={satellite.reload} title="Satellite" minHeight="8rem">
          {satellite.data && (
            <div className="space-y-3">
              {chips.length ? (
                <div className="flex flex-wrap gap-2">
                  {chips.map((c) => (
                    <figure key={c.date} className="text-center text-[10px] text-gray-500">
                      <div className="flex gap-1">
                        {c.kinds.includes("truecolor") && <RetryImg src={assetUrl(`/chips/${encodeURIComponent(uid)}/${c.date}.png?kind=truecolor`)} alt={`${c.date} true colour`} className="h-16 w-16 rounded border object-cover" />}
                        {c.kinds.includes("ndvi") && <RetryImg src={assetUrl(`/chips/${encodeURIComponent(uid)}/${c.date}.png?kind=ndvi`)} alt={`${c.date} NDVI`} className="h-16 w-16 rounded border object-cover" />}
                      </div>
                      <figcaption>{c.date}</figcaption>
                    </figure>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-400">No rendered chips for this parcel yet.</p>
              )}
              <DidTable rows={didRows} />
              <p className="text-xs text-gray-600">
                <b>What this means:</b> we don&rsquo;t ask &ldquo;is the field green&rdquo; (everything greens up after the monsoon) — we
                ask whether this parcel keeps behaving like never-acquired farmland nearby (the control group) after
                each legal event. A significant drop vs controls suggests possession changed something; keeping pace
                (plus a ploughing signal) suggests continued farming. <b>Caveat:</b> this is a lead for field
                verification, not a legal conclusion — 10 m pixels under-detect ploughing, and most parcels only have
                one post-possession season of data.
              </p>
            </div>
          )}
        </Widget>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-600">Findings</h2>
        <Widget status={findings.status} error={findings.error} onRetry={findings.reload} title="Findings" minHeight="4rem" emptyReason="No findings recorded for this parcel.">
          <ul className="space-y-2">
            {(findings.data || []).map((f) => (
              <li key={f.id} className="rounded border border-gray-200 p-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{f.category} · {f.severity}</span>
                  <ConfidencePill value={f.confidence} />
                </div>
                <p className="mt-0.5 text-xs text-gray-600">{f.title}</p>
                {f.caveats?.length ? <p className="mt-1 text-xs text-amber-700">{f.caveats.join("; ")}</p> : null}
                <button onClick={() => navigate(`/findings?evidence=${f.id}`)} className="mt-1 text-xs text-emerald-700 underline">Open evidence pack</button>
              </li>
            ))}
          </ul>
        </Widget>
      </section>
    </div>
  );
}
