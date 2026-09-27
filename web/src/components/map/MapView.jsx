import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { apiFetch, assetUrl } from "../../api/client.js";
import { categoricalStyle, sequentialStyle, propertyAvailable } from "../../lib/colorScale.js";
import { Legend } from "./Legend.jsx";
import { Widget } from "../common/Widget.jsx";

// Free vector basemap, no token (D-011 / phase6 "no paid services"). OpenFreeMap serves
// OSM-derived vector tiles at no cost and needs no API key.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const PARK_BBOX = [77.985, 8.79, 78.02, 8.825]; // Allikulam SIPCOT park, approx (fallback fit)

/**
 * Renders `spec.map.layers` (phase6 SKILL: "MapView | Layers from spec.map.layers"). Fetches
 * each layer's GeoJSON from `/layers/{ref_layer}.geojson` unless inline `geojson` is given.
 * @param {{layers: Array<{id:string,title:string,kind:string,ref_layer?:string,geojson?:object,color_by?:string,defaultVisible?:boolean,style?:string}>, colorBy?: string, onParcelClick?: (uid:string, feature:object)=>void, selectedParcelUid?: string, height?: string}} props
 */
export function MapView({ layers, colorBy, onParcelClick, selectedParcelUid, season, height = "32rem" }) {
  const containerRef = useRef(null);
  const loadedSeasonRef = useRef(null);   // season whose data the parcel layer currently holds
  const mapRef = useRef(null);
  const hoverPopupRef = useRef(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState(null);
  const [legend, setLegend] = useState({ title: "Legend", items: [] });
  const [layerErrors, setLayerErrors] = useState({});
  const [visible, setVisible] = useState(() => Object.fromEntries(layers.map((l) => [l.id, l.defaultVisible !== false])));
  const dataRef = useRef({});

  // toggle visibility checkboxes stay in sync if the layer list changes
  useEffect(() => {
    setVisible((v) => ({ ...Object.fromEntries(layers.map((l) => [l.id, l.defaultVisible !== false])), ...v }));
  }, [layers.map((l) => l.id).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      bounds: PARK_BBOX,
      fitBoundsOptions: { padding: 40 },
      attributionControl: true,
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    map.on("load", async () => {
      const failed = {};
      for (const layer of layers) {
        try {
          const params = layer.kind === "parcels" && season ? { season } : undefined;
          const fc = layer.geojson || (await loadLayerGeojson(layer.ref_layer, params));
          if (cancelled) return;
          dataRef.current[layer.id] = fc;
          addLayerToMap(map, layer, fc);
        } catch (e) {
          failed[layer.id] = String(e?.message || e);
        }
      }
      if (cancelled) return;
      setLayerErrors(failed);
      if (Object.keys(failed).length === layers.length && layers.length > 0) {
        setError({ message: "No layer could be loaded — is the API running?" });
        setStatus("error");
        return;
      }
      if (season) loadedSeasonRef.current = season;
      applyColorBy(map, layers, dataRef.current, colorBy, setLegend);
      setStatus("ok");
    });

    map.on("click", (e) => {
      const parcelLayerIds = layers.filter((l) => l.kind === "parcels").map((l) => `${l.id}-fill`);
      const feats = map.queryRenderedFeatures(e.point, { layers: parcelLayerIds.filter((id) => map.getLayer(id)) });
      if (feats.length) {
        const uid = feats[0].properties.parcel_uid;
        onParcelClick?.(uid, feats[0].properties);
      }
    });

    map.on("mousemove", (e) => {
      const parcelLayerIds = layers.filter((l) => l.kind === "parcels").map((l) => `${l.id}-fill`);
      const feats = map.queryRenderedFeatures(e.point, { layers: parcelLayerIds.filter((id) => map.getLayer(id)) });
      map.getCanvas().style.cursor = feats.length ? "pointer" : "";
      if (!hoverPopupRef.current) {
        hoverPopupRef.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
      }
      if (feats.length) {
        const p = feats[0].properties;
        const village = String(p.parcel_uid || "").split("|")[0];
        hoverPopupRef.current
          .setLngLat(e.lngLat)
          .setHTML(
            `<div class="text-xs"><b>${p.parcel_uid ?? "—"}</b><br/>village: ${village || "—"}<br/>area: ${p.area_ha_gis ?? "—"} ha<br/>stage: ${p.current_stage ?? "—"}<br/>findings: ${p.n_findings ?? "—"}${p.season_state ? `<br/>season state: ${p.season_state}` : ""}${p.stage_at_season ? `<br/>stage that season: ${p.stage_at_season}` : ""}</div>`
          )
          .addTo(map);
      } else {
        hoverPopupRef.current.remove();
      }
    });

    return () => {
      cancelled = true;
      hoverPopupRef.current?.remove();
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.map((l) => l.id + (l.ref_layer || "")).join(",")]);

  // Seasonal colourings (land use, stage by season) need the parcel layer re-fetched with ?season=…
  // (app/api/layers.py). Fetch first, then colour, so the legend never shows "not available" while
  // the season's data is on its way. Non-seasonal colourings just repaint the data already loaded.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || status !== "ok") return;
    const parcelLayers = layers.filter((l) => l.kind === "parcels");
    const hasProp = parcelLayers.every((l) => propertyAvailable(dataRef.current[l.id]?.features || [], colorBy?.property));
    if (!colorBy?.seasonal || !season || (loadedSeasonRef.current === season && hasProp)) {
      applyColorBy(map, layers, dataRef.current, colorBy, setLegend);
      return;
    }
    let cancelled = false;
    setLegend({ title: colorBy.label, items: [], note: `Loading ${season}…` });
    (async () => {
      for (const layer of parcelLayers) {
        const res = await apiFetch(`/layers/${layer.ref_layer}.geojson`, { params: { season } });
        if (cancelled || !res.ok) continue;
        dataRef.current[layer.id] = res.data;
        map.getSource(`src-${layer.id}`)?.setData(res.data);
      }
      if (!cancelled) {
        loadedSeasonRef.current = season;
        applyColorBy(map, layers, dataRef.current, colorBy, setLegend);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorBy, season, status]);

  // highlight selection
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("selected-outline")) return;
    if (selectedParcelUid) {
      map.setFilter("selected-outline", ["==", ["get", "parcel_uid"], selectedParcelUid]);
      map.setLayoutProperty("selected-outline", "visibility", "visible");
    } else {
      map.setLayoutProperty("selected-outline", "visibility", "none");
    }
  }, [selectedParcelUid, status]);

  function toggleLayer(id) {
    setVisible((v) => {
      const next = { ...v, [id]: !v[id] };
      const map = mapRef.current;
      const suffixes = ["-fill", "-line", "-circle"];
      suffixes.forEach((sfx) => {
        if (map?.getLayer(`${id}${sfx}`)) map.setLayoutProperty(`${id}${sfx}`, "visibility", next[id] ? "visible" : "none");
      });
      return next;
    });
  }

  return (
    <div className="relative" style={{ height }}>
      <div ref={containerRef} className="h-full w-full rounded-lg border border-gray-200" data-testid="maplibre-map" />
      {status === "loading" && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70">
          <Widget status="loading" minHeight="6rem" />
        </div>
      )}
      {status === "error" && (
        <div className="absolute inset-0 z-10 bg-white/90 p-4">
          <Widget status="error" error={error} minHeight="6rem" />
        </div>
      )}
      <div className="absolute right-3 top-3 z-10 max-h-[70%] overflow-auto rounded-lg border border-gray-200 bg-white/95 p-2 text-xs shadow">
        <div className="mb-1 font-semibold text-gray-700">Layers</div>
        {layers.map((l) => (
          <label key={l.id} className={`flex items-center gap-1.5 py-0.5 ${layerErrors[l.id] ? "text-gray-400" : ""}`} title={layerErrors[l.id]}>
            <input type="checkbox" checked={!!visible[l.id]} disabled={!!layerErrors[l.id]} onChange={() => toggleLayer(l.id)} />
            {l.title}
            {layerErrors[l.id] && <span className="text-[10px] italic">(unavailable)</span>}
          </label>
        ))}
      </div>
      {status === "ok" ? (
        <Legend title={legend.title} items={legend.items} note={legend.note} />
      ) : status === "loading" ? (
        <Legend title="Legend" items={[]} note="Loading…" />
      ) : null}
    </div>
  );
}

async function loadLayerGeojson(refLayer, params) {
  const res = await apiFetch(`/layers/${refLayer}.geojson`, { params });
  if (!res.ok) throw new Error(res.message);
  return res.data;
}

function addLayerToMap(map, layer, fc) {
  const sourceId = `src-${layer.id}`;
  if (map.getSource(sourceId)) return;
  map.addSource(sourceId, { type: "geojson", data: fc || { type: "FeatureCollection", features: [] } });
  const geomType = fc?.features?.[0]?.geometry?.type || "Polygon";

  if (layer.kind === "parcels") {
    map.addLayer({ id: `${layer.id}-fill`, type: "fill", source: sourceId, paint: { "fill-color": "#377eb8", "fill-opacity": 0.45 } });
    map.addLayer({ id: `${layer.id}-line`, type: "line", source: sourceId, paint: { "line-color": "#1f2937", "line-width": 0.5 } });
    if (!map.getLayer("selected-outline")) {
      map.addLayer({
        id: "selected-outline",
        type: "line",
        source: sourceId,
        paint: { "line-color": "#dc2626", "line-width": 3 },
        filter: ["==", ["get", "parcel_uid"], "__none__"],
        layout: { visibility: "none" },
      });
    }
  } else if (geomType.includes("Polygon")) {
    map.addLayer({ id: `${layer.id}-line`, type: "line", source: sourceId, paint: { "line-color": layer.color || "#059669", "line-width": 2 } });
    map.addLayer({ id: `${layer.id}-fill`, type: "fill", source: sourceId, paint: { "fill-color": layer.color || "#059669", "fill-opacity": 0.08 } });
  } else if (geomType === "LineString" || geomType === "MultiLineString") {
    // MapLibre's style validator rejects a `filter` key whose value is `undefined` (as opposed to
    // simply omitting the key) — only attach it when the layer actually defines one.
    map.addLayer({
      id: `${layer.id}-line`, type: "line", source: sourceId,
      paint: { "line-color": layer.color || "#f97316", "line-width": layer.lineWidth || 1.5 },
      ...(layer.filter ? { filter: layer.filter } : {}),
    });
  } else {
    map.addLayer({
      id: `${layer.id}-circle`, type: "circle", source: sourceId,
      paint: { "circle-color": layer.color || "#7c3aed", "circle-radius": 4 },
      ...(layer.filter ? { filter: layer.filter } : {}),
    });
  }
  if (layer.defaultVisible === false) {
    ["-fill", "-line", "-circle"].forEach((sfx) => {
      if (map.getLayer(`${layer.id}${sfx}`)) map.setLayoutProperty(`${layer.id}${sfx}`, "visibility", "none");
    });
  }
}

function applyColorBy(map, layers, dataMap, colorBy, setLegend) {
  const parcelLayer = layers.find((l) => l.kind === "parcels");
  if (!parcelLayer || !map.getLayer(`${parcelLayer.id}-fill`)) return;
  const features = dataMap[parcelLayer.id]?.features || [];
  if (!colorBy || colorBy === "none") {
    map.setPaintProperty(`${parcelLayer.id}-fill`, "fill-color", "#377eb8");
    setLegend({ title: "Legend", items: [{ value: "parcel", label: "Parcel (FMB)", color: "#377eb8" }] });
    return;
  }
  if (!propertyAvailable(features, colorBy.property)) {
    map.setPaintProperty(`${parcelLayer.id}-fill`, "fill-color", "#cbd5e1");
    setLegend({ title: colorBy.label, items: [], note: `Not available yet: '${colorBy.property}' is not present on /layers/${parcelLayer.ref_layer}.geojson.` });
    return;
  }
  const style = colorBy.scale === "sequential" ? sequentialStyle(features, colorBy.property, colorBy.order) : categoricalStyle(features, colorBy.property, colorBy.order);
  map.setPaintProperty(`${parcelLayer.id}-fill`, "fill-color", style.paint);
  setLegend({ title: colorBy.label, items: style.legend });
}

export { assetUrl };
