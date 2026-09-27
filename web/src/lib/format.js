export function fmtNumber(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: digits });
}

export function fmtPct(n, digits = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${(Number(n) * 100).toFixed(digits)}%`;
}

export function fmtDate(d) {
  if (!d) return "—";
  try {
    return new Date(d).toISOString().slice(0, 10);
  } catch {
    return String(d);
  }
}

export function fmtUsd(n, digits = 4) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `$${Number(n).toFixed(digits)}`;
}

/** Confidence -> a Tailwind colour class, used consistently across widgets. */
export function confidenceColor(c) {
  if (c === null || c === undefined) return "bg-gray-300";
  if (c >= 0.8) return "bg-emerald-500";
  if (c >= 0.5) return "bg-amber-500";
  return "bg-rose-500";
}

export function verdictColor(v) {
  switch (v) {
    case "ACCEPT":
      return "bg-emerald-100 text-emerald-800 border-emerald-300";
    case "DOWNGRADE":
      return "bg-amber-100 text-amber-800 border-amber-300";
    case "REVIEW":
      return "bg-gray-200 text-gray-700 border-gray-300";
    case "REROUTE":
      return "bg-sky-100 text-sky-800 border-sky-300";
    default:
      return "bg-gray-100 text-gray-600 border-gray-300";
  }
}
