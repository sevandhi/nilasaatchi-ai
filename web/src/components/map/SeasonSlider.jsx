import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";

// Kharif (Jun-Sep) / Rabi (Oct-Feb) / Summer (Mar-May) of ag-year Y (Jun Y .. May Y+1).
// Sentinel-2 data starts in Jan 2019, so the first season is 2018-rabi (Oct 2018-Feb 2019, partial)
// and 2018-kharif (no imagery) is skipped.
function buildSeasons() {
  const seasons = ["2018-rabi", "2018-summer"];
  for (let y = 2019; y <= 2026; y++) {
    seasons.push(`${y}-kharif`, `${y}-rabi`, `${y}-summer`);
  }
  return seasons;
}
export const PARTIAL_SEASONS = { "2018-rabi": "satellite imagery starts in Jan 2019, so this season is partial" };
const SEASONS = buildSeasons();

/** Scrubs seasons 2019->now (phase6 must-have). Recolouring the map by season land-use state
 * depends on a per-season `landuse_state` property the backend has not added to
 * /layers/parcel.geojson yet — the slider still drives `filters.season` honestly. */
export function SeasonSlider({ onChange, recolorNote }) {
  const { filters, setFilter, lang } = useStore((s) => s);
  const idx = Math.max(0, SEASONS.indexOf(filters.season));
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!playing) {
      clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(() => {
      const st = useStore.getState();
      // wait until the map is showing the current season before moving on (smooth, no skipped frames)
      if (st.mapSeason && st.mapSeason !== st.filters.season) return;
      const cur = Math.max(0, SEASONS.indexOf(st.filters.season));
      const next = SEASONS[(cur + 1) % SEASONS.length];
      setFilter("season", next);
      onChange?.(next);
    }, 700);
    return () => clearInterval(timerRef.current);
  }, [playing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-2" data-testid="season-slider">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setPlaying((p) => !p)}
          className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white"
          data-testid="season-play"
        >
          {playing ? t("pause", lang) : t("play", lang)}
        </button>
        <input
          type="range"
          min={0}
          max={SEASONS.length - 1}
          value={idx}
          onChange={(e) => {
            const next = SEASONS[Number(e.target.value)];
            setFilter("season", next);
            onChange?.(next);
          }}
          className="flex-1"
        />
        <span className="w-20 text-right text-xs font-medium text-gray-700">{filters.season}</span>
      </div>
      {recolorNote && <p className="mt-1 text-[11px] text-gray-400">{recolorNote}</p>}
    </div>
  );
}

export { SEASONS };
