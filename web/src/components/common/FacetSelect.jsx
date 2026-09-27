/**
 * A filter dropdown whose options come from a live `/…/facets` endpoint ({value, count} from the
 * database), so new values (e.g. from an upload) appear automatically. "All" clears the filter.
 */
export function FacetSelect({ label, options, labels = {}, value, onChange, testId }) {
  return (
    <label className="flex items-center gap-1 text-gray-500">
      {label}:
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || "")}
        className="max-w-[15rem] rounded border border-gray-300 px-1 py-0.5 text-gray-800"
        data-testid={testId || `filter-${label.toLowerCase().replace(/\s+/g, "-")}`}
      >
        <option value="">All</option>
        {(options || []).map((o) => (
          <option key={String(o.value)} value={o.value}>{labels[o.value] || o.value} ({o.count})</option>
        ))}
      </select>
    </label>
  );
}
