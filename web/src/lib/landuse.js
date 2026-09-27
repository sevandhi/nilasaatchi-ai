// Land-use states from the student model (docs/team/07-phase3-satellite-evidence.md §4).
export const LANDUSE_STATES = ["cropped", "irrigated_multi", "perennial_veg", "bare_fallow", "cleared_or_built", "water", "insufficient_data"];
export const LANDUSE_COLORS = {
  cropped: "#4ade80",
  irrigated_multi: "#16a34a",
  perennial_veg: "#065f46",
  bare_fallow: "#d6b370",
  cleared_or_built: "#9ca3af",
  water: "#38bdf8",
  insufficient_data: "#e5e7eb",
};

/**
 * Kharif (Jun–Sep) / Rabi (Oct–Feb, crosses the year boundary) / Summer (Mar–May) —
 * docs/team/07-phase3-satellite-evidence.md §3. `parcel_season.ag_year` is the year the
 * agricultural year starts in; `annual` is a whole-year aggregate row, not a plottable band.
 * @returns {[string,string]|null} [startISO, endISO)
 */
export function seasonDateRange(agYear, season) {
  switch (season) {
    case "kharif":
      return [`${agYear}-06-01`, `${agYear}-10-01`];
    case "rabi":
      return [`${agYear}-10-01`, `${agYear + 1}-03-01`];
    case "summer":
      return [`${agYear + 1}-03-01`, `${agYear + 1}-06-01`];
    default:
      return null; // e.g. "annual" — a same-period aggregate, would overlap the three bands above
  }
}
