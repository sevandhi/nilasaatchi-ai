// Shared categorical/sequential colour scales for the map and charts (phase6 spec §2:
// "Styles: by_stage (10-colour categorical, stage order), by_category ..., by_match ...,
// by_score (sequential)").
export const CATEGORICAL_10 = [
  "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e",
  "#e6ab02", "#a6761d", "#666666", "#377eb8", "#984ea3",
];

export const SEQUENTIAL_5 = ["#f1eef6", "#bdc9e1", "#74a9cf", "#2b8cbe", "#045a8d"];

// Real distinct `current_stage` values from `v_parcel_lifecycle` (checked live against the API).
export const STAGE_ORDER = ["SEC_3_2", "AWARD", "PAYMENT", "POSSESSION", "MUTATION"];

export const MATCH_COLORS = { matched: "#1b9e77", unmatched: "#d95f02", extent_diff: "#e7298a", owner_diff: "#7570b3" };

/**
 * Inspects a sample of features for whether `property` is actually present (honesty guard —
 * we never invent a colour scale for data the backend hasn't produced yet).
 */
export function propertyAvailable(features, property) {
  if (!features?.length) return false;
  return features.some((f) => f.properties && Object.prototype.hasOwnProperty.call(f.properties, property) && f.properties[property] !== null);
}

/** Builds a MapLibre `fill-color` expression + legend items for a categorical property. */
const NEUTRAL_VALUES = { NOT_STARTED: "#d1d5db", "No documents linked": "#d1d5db", "No possession evidence": "#d1d5db",
  "No significant change": "#9ca3af" };

export function categoricalStyle(features, property, order) {
  const values = new Set();
  for (const f of features) {
    const v = f.properties?.[property];
    if (v !== null && v !== undefined) values.add(v);
  }
  const ordered = order ? order.filter((v) => values.has(v)).concat([...values].filter((v) => !order.includes(v))) : [...values];
  // stable colours: a value keeps its colour however many other values are on screen (e.g. while the
  // season slider moves); "nothing yet / no data" values are grey
  const slot = (v, i) => (order && order.includes(v) ? order.indexOf(v) : (order ? order.length : 0) + i);
  const legend = ordered.map((v, i) => ({ value: v, label: String(v),
    color: NEUTRAL_VALUES[v] || CATEGORICAL_10[slot(v, i) % CATEGORICAL_10.length] }));
  const expr = ["match", ["get", property]];
  legend.forEach((l) => expr.push(l.value, l.color));
  expr.push("#cccccc");
  return { paint: expr, legend };
}

/**
 * Assigns a stable colour per distinct value (e.g. acquisition-event stage), preferring `order`
 * for the ones it knows about, so the same stage gets the same colour on the map and on the
 * Parcel page's timeline. Returns a `{value -> color}` lookup plus a compact legend.
 */
export function categoricalColorMap(values, order = STAGE_ORDER) {
  const distinct = [...new Set(values.filter((v) => v !== null && v !== undefined))];
  const ordered = order.filter((v) => distinct.includes(v)).concat(distinct.filter((v) => !order.includes(v)));
  const byValue = {};
  const legend = ordered.map((v, i) => {
    const color = CATEGORICAL_10[i % CATEGORICAL_10.length];
    byValue[v] = color;
    return { value: v, label: String(v), color };
  });
  return { byValue, legend };
}

/** Builds a sequential fill-color expression + legend for a numeric property. */
export function sequentialStyle(features, property) {
  const values = features.map((f) => f.properties?.[property]).filter((v) => typeof v === "number");
  if (!values.length) return { paint: "#cccccc", legend: [] };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const step = (max - min) / (SEQUENTIAL_5.length - 1) || 1;
  const expr = ["interpolate", ["linear"], ["get", property]];
  SEQUENTIAL_5.forEach((c, i) => expr.push(min + i * step, c));
  const legend = SEQUENTIAL_5.map((c, i) => ({ value: (min + i * step).toFixed(1), label: (min + i * step).toFixed(1), color: c }));
  return { paint: expr, legend };
}
