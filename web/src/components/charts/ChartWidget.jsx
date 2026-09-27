import ReactECharts from "echarts-for-react";
import { CATEGORICAL_10 } from "../../lib/colorScale.js";

/**
 * Renders one `spec.charts[i]` entry. Clicking a segment sets the crossfilter key
 * (phase6 SKILL: "Clicking a segment sets the crossfilter key").
 * @param {{spec: {id:string,title:string,type:string,x:string,y:string[],data:object[]}, onSegmentClick?: (key:string)=>void}} props
 */
export function ChartWidget({ spec, onSegmentClick }) {
  const categories = spec.data.map((d) => d[spec.x]);
  const option = buildOption(spec, categories);
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-2">
      <div className="mb-1 text-sm font-medium text-gray-700">{spec.title}</div>
      <ReactECharts
        option={option}
        style={{ height: 260 }}
        onEvents={{
          click: (params) => onSegmentClick?.(params.name ?? categories[params.dataIndex]),
        }}
      />
    </div>
  );
}

function buildOption(spec, categories) {
  const color = CATEGORICAL_10;
  if (spec.type === "pie") {
    const y = spec.y[0];
    return {
      color,
      tooltip: { trigger: "item" },
      legend: { bottom: 0, type: "scroll" },
      series: [{ type: "pie", radius: "60%", data: spec.data.map((d) => ({ name: d[spec.x], value: d[y] })) }],
    };
  }
  const seriesType = spec.type === "line" ? "line" : "bar";
  const stack = spec.type === "stacked_bar" ? "total" : undefined;
  // Widgets around this chart re-render often (async KPI/table fetches on the same page), each
  // passing a fresh `option` object; animation off avoids a rapid-rebuild rendering glitch where
  // a stacked bar could be caught mid grow-in and show only its top edge.
  return {
    color,
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { bottom: 0, type: "scroll" },
    grid: { left: 48, right: 16, top: 24, bottom: 48 },
    xAxis: { type: "category", data: categories, axisLabel: { rotate: categories.length > 8 ? 30 : 0 } },
    yAxis: { type: "value" },
    series: spec.y.map((k) => ({ name: k, type: seriesType, stack, data: spec.data.map((d) => d[k]) })),
  };
}
