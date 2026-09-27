import { useEffect, useRef, useState } from "react";
import { useStore } from "../../store/useStore.js";
import { t } from "../../i18n/labels.js";
import { it } from "./labels.js";
import { getSatelliteStatus, refreshSatellite } from "./api.js";
import { JobProgress } from "./JobProgress.jsx";
import { fmtDate, fmtNumber } from "../../lib/format.js";
import { READ_ONLY } from "../../api/client.js";

/**
 * Overview "data freshness" card: latest satellite scene date, scene count, last refresh, and a
 * button to kick off a satellite-refresh job with live progress. Mirrors the KpiCard visual
 * weight so it sits naturally next to the KPI grid.
 */
export function SatelliteFreshness() {
  const lang = useStore((s) => s.lang);
  const [status, setStatus] = useState({ phase: "loading", data: null, error: null });
  const [jobId, setJobId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [actionError, setActionError] = useState(null);
  // Guards against setState-after-unmount — React StrictMode's dev-only double mount/cleanup
  // cycle otherwise leaves a stray in-flight fetch from the discarded first mount.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  async function loadStatus() {
    setStatus((s) => ({ ...s, phase: "loading" }));
    const res = await getSatelliteStatus();
    if (!mountedRef.current) return;
    if (!res.ok) {
      setStatus({ phase: res.kind === "not_implemented" ? "not_implemented" : "error", data: null, error: res });
      return;
    }
    setStatus({ phase: "ok", data: res.data, error: null });
    if (res.data.running_job_id && !jobId) setJobId(res.data.running_job_id);
  }

  useEffect(() => {
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startRefresh() {
    setStarting(true);
    setActionError(null);
    const res = await refreshSatellite();
    if (!mountedRef.current) return;
    setStarting(false);
    if (!res.ok) {
      if (res.kind === "conflict") {
        setActionError(it("refresh_running", lang));
        loadStatus();
        return;
      }
      setActionError(res.kind === "not_implemented" ? it("upload_service_unavailable", lang) : res.message);
      return;
    }
    setJobId(res.data.job_id);
  }

  function handleSettled() {
    loadStatus();
  }

  if (status.phase === "loading") {
    return (
      <div className="animate-pulse rounded-lg border border-gray-200 bg-gray-50 p-3" style={{ minHeight: "6rem" }} role="status" aria-label={t("loading", lang)}>
        <div className="h-4 w-1/3 rounded bg-gray-200" />
        <div className="mt-3 h-3 w-2/3 rounded bg-gray-200" />
      </div>
    );
  }
  if (status.phase === "not_implemented") {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3 text-xs text-gray-500" data-testid="satellite-freshness-unavailable">
        <p className="font-medium text-gray-600">{it("freshness_title", lang)}</p>
        <p className="mt-1">{it("upload_service_unavailable", lang)}</p>
      </div>
    );
  }
  if (status.phase === "error") {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
        <p className="font-medium">{t("error", lang)}</p>
        <p className="mt-1 text-xs">{status.error?.message}</p>
        <button onClick={loadStatus} className="mt-2 rounded border border-rose-300 bg-white px-3 py-1 text-xs font-medium hover:bg-rose-100">
          {t("retry", lang)}
        </button>
      </div>
    );
  }

  const d = status.data || {};
  return (
    <div className="space-y-2 rounded-lg border border-gray-200 bg-white p-3" data-testid="satellite-freshness">
      <h2 className="text-sm font-semibold text-gray-600">{it("freshness_title", lang)}</h2>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-lg font-semibold text-gray-900">{d.latest_scene_date ? fmtDate(d.latest_scene_date) : "—"}</div>
          <div className="text-[10px] text-gray-500">{it("latest_scene", lang)}</div>
        </div>
        <div>
          <div className="text-lg font-semibold text-gray-900">{d.scene_count != null ? fmtNumber(d.scene_count) : "—"}</div>
          <div className="text-[10px] text-gray-500">{it("scene_count", lang)}</div>
        </div>
        <div>
          <div className="text-lg font-semibold text-gray-900">{d.last_refresh_at ? fmtDate(d.last_refresh_at) : "—"}</div>
          <div className="text-[10px] text-gray-500">{it("last_refresh", lang)}</div>
        </div>
      </div>
      {!READ_ONLY && (
        <button
          onClick={startRefresh}
          disabled={starting || !!jobId}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
          data-testid="satellite-refresh-button"
        >
          {starting ? it("checking", lang) : it("check_new_images", lang)}
        </button>
      )}
      {!READ_ONLY && actionError && <p className="text-xs text-rose-600" data-testid="satellite-refresh-error">{actionError}</p>}
      {!READ_ONLY && jobId && (
        <div className="mt-2">
          <JobProgress jobId={jobId} kind="satellite" onSettled={handleSettled} />
        </div>
      )}
    </div>
  );
}
