import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";

/**
 * Standard loading / empty / error / not-implemented / ok wrapper so every widget on every
 * page behaves the same way (phase6 UX quality bar).
 * @param {{status: "loading"|"empty"|"error"|"not_implemented"|"ok"|"idle", error?: any, onRetry?: ()=>void, emptyReason?: string, title?: string, children: any, minHeight?: string}} props
 */
export function Widget({ status, error, onRetry, emptyReason, title, children, minHeight = "8rem" }) {
  const lang = useStore((s) => s.lang);
  if (status === "loading" || status === "idle") {
    return (
      <div className="animate-pulse rounded-lg border border-gray-200 bg-gray-50 p-4" style={{ minHeight }} role="status" aria-label={t("loading", lang)}>
        <div className="h-4 w-1/3 rounded bg-gray-200" />
        <div className="mt-3 h-3 w-full rounded bg-gray-200" />
        <div className="mt-2 h-3 w-5/6 rounded bg-gray-200" />
        <div className="mt-2 h-3 w-2/3 rounded bg-gray-200" />
      </div>
    );
  }
  if (status === "not_implemented") {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4 text-sm text-gray-500" style={{ minHeight }}>
        <p className="font-medium text-gray-600">{title ? `${title}: ` : ""}{t("not_implemented", lang)}</p>
        <p className="mt-1">{error?.message || "This endpoint has not been built by the backend yet."}</p>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800" style={{ minHeight }}>
        <p className="font-medium">{t("error", lang)}</p>
        <p className="mt-1">{error?.message || "Unknown error."}</p>
        {onRetry && (
          <button onClick={onRetry} className="mt-2 rounded border border-rose-300 bg-white px-3 py-1 text-xs font-medium hover:bg-rose-100">
            {t("retry", lang)}
          </button>
        )}
      </div>
    );
  }
  if (status === "empty") {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500" style={{ minHeight }}>
        <p className="font-medium text-gray-600">{t("empty", lang)}</p>
        {emptyReason && <p className="mt-1">{emptyReason}</p>}
      </div>
    );
  }
  return children;
}
