import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { MapView } from "../components/map/MapView.jsx";
import { SeasonSlider } from "../components/map/SeasonSlider.jsx";
import { useStore } from "../store/useStore.js";
import { STAGE_ORDER } from "../lib/colorScale.js";
import { LANDUSE_STATES } from "../lib/landuse.js";

const COLOR_BY_OPTIONS = [
  { key: "none", label: "None (single colour)" },
  { key: "stage", label: "Acquisition stage (lifecycle)", property: "current_stage", scale: "categorical", order: STAGE_ORDER },
  { key: "finding_count", label: "Finding count (severity/category needs parcel drill-down)", property: "n_findings", scale: "sequential" },
  { key: "landuse_state", label: "Land-use state (season slider)", property: "season_state", scale: "categorical", order: LANDUSE_STATES },
  { key: "did_signal", label: "DiD vs controls (parcel-level only — see Parcel page)", property: "did_signal", scale: "sequential" },
  { key: "match_status", label: "Match status (from the last agent run only)", property: "match_status", scale: "categorical" },
];

const LAYERS = [
  { id: "parcels", title: "FMB parcels (1,242)", kind: "parcels", ref_layer: "parcel" },
  { id: "park", title: "Park boundary", kind: "reference", ref_layer: "ref_layer_park_boundary" },
  { id: "roads_major", title: "Roads — major", kind: "reference", ref_layer: "ref_layer_roads", filter: ["==", ["get", "is_major"], true], lineWidth: 2.5, color: "#b91c1c" },
  { id: "roads_other", title: "Roads — other", kind: "reference", ref_layer: "ref_layer_roads", filter: ["==", ["get", "is_major"], false], lineWidth: 1, color: "#f59e0b", defaultVisible: false },
  { id: "waterbodies", title: "Waterbodies", kind: "reference", ref_layer: "ref_layer_waterbodies", color: "#0284c7" },
  { id: "substations", title: "Substations", kind: "reference", ref_layer: "ref_layer_substations", color: "#7c3aed" },
  { id: "rail", title: "Rail", kind: "reference", ref_layer: "ref_layer_rail", color: "#4b5563" },
  { id: "schools", title: "Schools", kind: "reference", ref_layer: "ref_layer_schools", color: "#059669", defaultVisible: false },
  { id: "sipcot_parks", title: "SIPCOT parks", kind: "reference", ref_layer: "ref_layer_sipcot_parks", color: "#ea580c", defaultVisible: false },
  { id: "fmb_qa", title: "FMB QA issues (overlaps + cross-village)", kind: "reference", ref_layer: "fmb_qa", color: "#dc2626", defaultVisible: false },
  { id: "fmb_overlaps", title: "FMB overlaps only", kind: "reference", ref_layer: "fmb_overlaps", color: "#be185d", defaultVisible: false },
  { id: "outside_survey", title: "Parcels outside their survey", kind: "reference", ref_layer: "outside_survey", color: "#9333ea", defaultVisible: false },
  { id: "controls", title: "Control-group cells (2–6 km ring)", kind: "reference", ref_layer: "controls", color: "#0d9488", defaultVisible: false },
];

/** Page 2: Map workspace — base layers, colour-by, season slider, legend, click-to-parcel. */
export function MapWorkspace() {
  const navigate = useNavigate();
  const { selectParcel, colorBy: colorByKey, setColorBy, selection, filters } = useStore((s) => s);
  const colorBy = useMemo(() => COLOR_BY_OPTIONS.find((o) => o.key === colorByKey) || COLOR_BY_OPTIONS[0], [colorByKey]);
  const seasonForColor = colorBy.property === "season_state" ? filters.season : null;

  function onParcelClick(uid) {
    selectParcel(uid);
    navigate(`/parcel/${encodeURIComponent(uid)}`);
  }

  return (
    <div className="relative -m-4 h-[calc(100%+2rem)]">
      <MapView
        layers={LAYERS}
        colorBy={colorBy.key === "none" ? null : colorBy}
        season={seasonForColor}
        onParcelClick={onParcelClick}
        selectedParcelUid={selection.parcelUid}
        height="100%"
      />
      <div className="absolute left-3 top-3 z-10 w-[22rem] max-w-[calc(100%-1.5rem)] space-y-2 rounded-lg border border-gray-200 bg-white/95 p-3 shadow">
        <h1 className="text-sm font-semibold text-gray-900">Map workspace</h1>
        <label className="block text-xs font-medium text-gray-600">
          Colour by:{" "}
          <select value={colorByKey || "none"} onChange={(e) => setColorBy(e.target.value)} className="rounded border border-gray-300 px-2 py-1 text-xs" data-testid="colorby-select">
            {COLOR_BY_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </label>
        <SeasonSlider recolorNote={colorBy.property === "season_state" ? "The map shows each parcel's land-use state for the selected season." : "Choose \"Land-use state\" above to recolour the map by season."} />
        <p className="text-[11px] leading-snug text-gray-500">
          Click a parcel to open its Parcel page. Use the layer list (right) to show roads, water, substations, map-quality issues and the control-group farmland.
        </p>
      </div>
    </div>
  );
}
