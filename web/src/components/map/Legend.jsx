import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";

/** Always-visible legend (phase6 must-have). */
export function Legend({ title, items, note }) {
  const lang = useStore((s) => s.lang);
  return (
    <div className="absolute bottom-3 left-3 z-10 max-w-xs rounded-lg border border-gray-200 bg-white/95 p-2 text-xs shadow" data-testid="map-legend">
      <div className="mb-1 font-semibold text-gray-700">{title || t("legend", lang)}</div>
      {items && items.length ? (
        <ul className="space-y-0.5">
          {items.map((it) => (
            <li key={String(it.value)} className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: it.color }} />
              {it.label}
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-gray-400">{note || "No categories to show for this colour-by yet."}</div>
      )}
    </div>
  );
}
