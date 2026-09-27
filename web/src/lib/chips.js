/** Groups server-local chip file paths (e.g. from `/parcels/{uid}/satellite`'s `chips` list or
 * an evidence pack's `planet.chips.cached`) into {date, kinds:[truecolor,ndvi]} — the actual
 * pixels are always fetched via `GET /chips/{uid}/{date}.png?kind=`. */
export function chipDates(chipPaths) {
  const byDate = new Map();
  for (const p of chipPaths || []) {
    const file = p.split("/").pop() || "";
    const m = file.match(/^(\d{4}-\d{2}-\d{2})_(truecolor|ndvi)\.png$/);
    if (!m) continue;
    const [, date, kind] = m;
    if (!byDate.has(date)) byDate.set(date, new Set());
    byDate.get(date).add(kind);
  }
  return [...byDate.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([date, kinds]) => ({ date, kinds: [...kinds] }));
}
