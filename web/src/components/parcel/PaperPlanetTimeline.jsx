import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useApi } from "../../hooks/useApi.js";
import { Widget } from "../common/Widget.jsx";
import { LANDUSE_COLORS, seasonDateRange } from "../../lib/landuse.js";
import { categoricalColorMap } from "../../lib/colorScale.js";

/**
 * The signature "Paper vs Planet" view (phase6 SKILL / ui-spec §3): smoothed NDVI (+BSI
 * toggle), observation dots, document-event markers and season-state bands, for one parcel.
 * Reads `GET /parcels/{uid}/timeline` (real fields: `series[].{date,ndvi,bsi,supported}`,
 * `events[].{event_date,stage,document_id,extraction_id}`, `season_states[].{ag_year,season,state}`).
 * @param {{parcelUid: string, onEventClick?: (evt:object)=>void, onDotClick?: (date:string)=>void}} props
 */
export function PaperPlanetTimeline({ parcelUid, onEventClick, onDotClick }) {
  const { status, data, error, reload } = useApi(parcelUid ? `/parcels/${encodeURIComponent(parcelUid)}/timeline` : null, {}, [parcelUid]);
  const [showBsi, setShowBsi] = useState(false);

  const built = useMemo(() => (data ? buildOption(data, showBsi) : null), [data, showBsi]);
  const states = useMemo(() => [...new Set((data?.season_states || []).map((s) => s.state).filter(Boolean))], [data]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3" data-testid="paper-planet-timeline">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">Paper vs Planet timeline</h3>
        <label className="flex items-center gap-1 text-xs text-gray-500">
          <input type="checkbox" checked={showBsi} onChange={() => setShowBsi((v) => !v)} /> show BSI (bare-soil index)
        </label>
      </div>
      <Widget status={status} error={error} onRetry={reload} minHeight="22rem" emptyReason="No satellite/document series for this parcel.">
        {built && (
          <>
            <ReactECharts
              option={built.option}
              style={{ height: 360 }}
              onEvents={{
                click: (p) => {
                  if (p.seriesName === "Document events") onEventClick?.(built.eventGroups[p.dataIndex]?.events?.[0]);
                  else if (p.seriesName === "Observations") onDotClick?.(data.series.filter((d) => d.supported)[p.dataIndex]?.date);
                },
              }}
            />
            <div className="mt-2 flex flex-wrap items-start justify-between gap-x-6 gap-y-1 text-[11px] text-gray-500">
              {states.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">Season state:</span>
                  {states.map((s) => (
                    <span key={s} className="flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: LANDUSE_COLORS[s] || "#e5e7eb" }} />
                      {s}
                    </span>
                  ))}
                </div>
              )}
              {built.stageLegend.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">Document events:</span>
                  {built.stageLegend.map((l) => (
                    <span key={l.value} className="flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: l.color }} />
                      {l.label}
                    </span>
                  ))}
                  <span className="text-gray-400">(hover a marker for the date; a number badge means several events share that date)</span>
                </div>
              )}
            </div>
          </>
        )}
      </Widget>
    </div>
  );
}

function buildOption(data, showBsi) {
  const series = data.series || [];
  const observed = series.filter((d) => d.supported);
  const events = (data.events || []).filter((e) => e.event_date);
  const maxY = Math.max(0.1, ...series.map((d) => d.ndvi ?? 0));

  const markAreas = (data.season_states || [])
    .map((s) => ({ range: seasonDateRange(s.ag_year, s.season), state: s.state }))
    .filter((s) => s.range)
    .map((s) => [{ xAxis: s.range[0], itemStyle: { color: (LANDUSE_COLORS[s.state] || "#e5e7eb") + "55" } }, { xAxis: s.range[1] }]);

  // De-duplicate same-date/same-stage events into one marker with a count, and colour markers by
  // stage (a compact legend, rather than an unreadable label per marker) — coordinator fix #2.
  const groupsByKey = new Map();
  for (const e of events) {
    const key = `${e.event_date}|${e.stage}`;
    if (!groupsByKey.has(key)) groupsByKey.set(key, { date: e.event_date, stage: e.stage, events: [] });
    groupsByKey.get(key).events.push(e);
  }
  const eventGroups = [...groupsByKey.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
  const { byValue: stageColor, legend: stageLegend } = categoricalColorMap(eventGroups.map((g) => g.stage));

  // The smoothed line only connects `supported` (real-observation-backed) points — an explicit
  // `null` at unsupported dates breaks the line and (with connectNulls: false, the default)
  // resumes cleanly afterwards, rather than silently interpolating through long unsupported
  // stretches as if they were equally trustworthy (coordinator fix #1).
  const lineData = series.map((d) => [d.date, d.supported ? d.ndvi : null]);

  const chartSeries = [
    {
      name: "NDVI (smoothed)",
      type: "line",
      showSymbol: false,
      connectNulls: false,
      data: lineData,
      lineStyle: { color: "#166534" },
      markArea: { silent: true, data: markAreas },
    },
    {
      name: "Observations",
      type: "scatter",
      symbolSize: 4,
      data: observed.map((d) => [d.date, d.ndvi]),
      itemStyle: { color: "#166534" },
    },
    {
      name: "Document events",
      type: "scatter",
      symbol: "circle",
      data: eventGroups.map((g) => ({
        value: [g.date, maxY * 1.08],
        symbolSize: g.events.length > 1 ? 16 : 10,
        itemStyle: { color: stageColor[g.stage] || "#b91c1c", borderColor: "#fff", borderWidth: 1 },
        label: { show: g.events.length > 1, formatter: String(g.events.length), color: "#fff", fontSize: 9, fontWeight: "bold" },
        stage: g.stage,
        date: g.date,
        count: g.events.length,
      })),
      tooltip: {
        formatter: (p) => `<b>${p.data.stage}</b><br/>${p.data.date}${p.data.count > 1 ? `<br/>${p.data.count} events on this date/stage` : ""}`,
      },
    },
  ];
  if (showBsi) {
    chartSeries.push({ name: "BSI", type: "line", yAxisIndex: 1, showSymbol: false, connectNulls: false, data: series.map((d) => [d.date, d.supported ? d.bsi : null]), lineStyle: { color: "#a16207", type: "dashed" } });
  }

  const option = {
    animation: false, // avoid a rebuild-mid-paint glitch on the frequent re-renders this page does
    tooltip: { trigger: "item" },
    legend: { bottom: 0, type: "scroll" },
    grid: { left: 48, right: 48, top: 24, bottom: 56 },
    xAxis: { type: "time" },
    yAxis: [{ type: "value", name: "NDVI" }, { type: "value", name: "BSI", show: showBsi }],
    series: chartSeries,
  };
  return { option, eventGroups, stageLegend };
}
