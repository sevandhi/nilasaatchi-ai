import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useStore } from "../../store/useStore.js";
import { it } from "./labels.js";
import { getIngestJob } from "./api.js";
import { API_BASE } from "../../api/client.js";

const STAGE_LABELS = {
  store: { en: "Saving file", ta: "கோப்பை சேமிக்கிறது" },
  catalog: { en: "Cataloguing", ta: "பட்டியலிடுகிறது" },
  classify: { en: "Classifying document type", ta: "ஆவண வகையை வகைப்படுத்துகிறது" },
  extract: { en: "Reading tables", ta: "அட்டவணைகளைப் படிக்கிறது" },
  load: { en: "Loading extracted data", ta: "பிரித்தெடுத்த தரவை ஏற்றுகிறது" },
  match: { en: "Linking to parcels", ta: "நிலத்துண்டுகளுடன் இணைக்கிறது" },
  findings: { en: "Checking findings", ta: "கண்டறிதல்களை சரிபார்க்கிறது" },
  inventory: { en: "Checking for new images", ta: "புதிய படங்களை சரிபார்க்கிறது" },
  features: { en: "Computing features", ta: "பண்புகளை கணக்கிடுகிறது" },
};

const STAGE_ICON = {
  pending: { glyph: "○", cls: "text-gray-300" },
  running: { glyph: "◐", cls: "animate-pulse text-sky-600" },
  done: { glyph: "✓", cls: "text-emerald-600" },
  skipped: { glyph: "–", cls: "text-gray-300" },
  failed: { glyph: "✕", cls: "text-rose-600" },
};

const JOB_STATUS_LABEL = {
  queued: { en: "Queued", ta: "வரிசையில்" },
  running: { en: "Processing", ta: "செயலாக்கத்தில்" },
  done: { en: "Done", ta: "முடிந்தது" },
  failed: { en: "Failed", ta: "தோல்வி" },
  duplicate: { en: "Duplicate", ta: "நகல்" },
};

function stageLabel(name, lang) {
  return STAGE_LABELS[name]?.[lang] || STAGE_LABELS[name]?.en || name;
}

export function JobStatusBadge({ status }) {
  const lang = useStore((s) => s.lang);
  const cls =
    status === "done"
      ? "bg-emerald-100 text-emerald-800"
      : status === "failed"
        ? "bg-rose-100 text-rose-800"
        : status === "duplicate"
          ? "bg-amber-100 text-amber-800"
          : status === "running"
            ? "bg-sky-100 text-sky-800"
            : "bg-gray-200 text-gray-700";
  const label = JOB_STATUS_LABEL[status]?.[lang] || JOB_STATUS_LABEL[status]?.en || status || "—";
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{label}</span>;
}

function duration(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

const TERMINAL = new Set(["done", "failed", "duplicate"]);

/**
 * Polls GET /ingest/jobs/{id} every 2s until the job reaches a terminal state, and renders a
 * stage stepper + a kind-specific result summary. Stops polling on unmount.
 * @param {{jobId: string|number, kind: "document"|"satellite", initialJob?: object, onSettled?: (job:object)=>void}} props
 */
export function JobProgress({ jobId, kind, initialJob = null, onSettled }) {
  const lang = useStore((s) => s.lang);
  const [job, setJob] = useState(initialJob);
  const [status, setStatus] = useState(initialJob ? "ok" : "loading");
  const [error, setError] = useState(null);
  const settledRef = useRef(false);

  useEffect(() => {
    settledRef.current = false;
    if (!jobId) return;
    let cancelled = false;
    let timer = null;

    async function tick() {
      const res = await getIngestJob(jobId);
      if (cancelled) return;
      if (!res.ok) {
        setStatus(res.kind === "not_implemented" ? "not_implemented" : "error");
        setError(res);
        return;
      }
      setJob(res.data);
      setStatus("ok");
      if (TERMINAL.has(res.data.status)) {
        if (!settledRef.current) {
          settledRef.current = true;
          onSettled?.(res.data);
        }
      } else {
        timer = setTimeout(tick, 2000);
      }
    }
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  if (status === "loading") {
    return (
      <div className="animate-pulse rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-400" role="status">
        {it("stage_title", lang)}…
      </div>
    );
  }
  if (status === "not_implemented") {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3 text-xs text-gray-500">
        {it("upload_service_unavailable", lang)}
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
        <p className="font-medium">Could not load job status.</p>
        <p className="mt-1 text-xs">{error?.message}</p>
      </div>
    );
  }
  if (!job) return null;

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-3" data-testid="job-progress">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-gray-400">job {job.id}</span>
        <JobStatusBadge status={job.status} />
        {job.filename && <span className="truncate text-xs text-gray-500">{job.filename}</span>}
      </div>

      {job.status === "duplicate" ? (
        <DuplicateResult lang={lang} />
      ) : job.status === "failed" ? (
        <FailedResult job={job} lang={lang} />
      ) : (
        <>
          <ol className="space-y-1.5" data-testid="stage-list">
            {(job.stages || []).map((s) => {
              const icon = STAGE_ICON[s.status] || STAGE_ICON.pending;
              const dur = duration(s.started_at, s.finished_at);
              return (
                <li key={s.name} className="flex items-start gap-2 text-xs" data-testid={`stage-${s.name}`} data-stage-status={s.status}>
                  <span className={`w-4 shrink-0 text-center ${icon.cls}`}>{icon.glyph}</span>
                  <div className="min-w-0">
                    <span className="text-gray-700">{stageLabel(s.name, lang)}</span>
                    {dur && <span className="ml-1 text-gray-400">({dur})</span>}
                    {s.detail && <div className="text-gray-400">{s.detail}</div>}
                  </div>
                </li>
              );
            })}
          </ol>
          {job.status === "done" && job.result && (kind === "satellite" ? <SatelliteResult result={job.result} lang={lang} /> : <DocumentResult result={job.result} jobId={job.id} lang={lang} />)}
        </>
      )}
    </div>
  );
}

function DuplicateResult({ lang }) {
  return (
    <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900" data-testid="duplicate-result">
      <p className="font-medium">{it("duplicate_title", lang)}</p>
      <Link to="/documents" className="mt-1 inline-block underline">
        {it("duplicate_link", lang)}
      </Link>
    </div>
  );
}

function FailedResult({ job, lang }) {
  const failedStage = (job.stages || []).find((s) => s.status === "failed");
  return (
    <div className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-900" data-testid="failed-result">
      <p className="font-medium">{it("failed_title", lang)}</p>
      {failedStage && <p className="mt-1">{stageLabel(failedStage.name, lang)}: {failedStage.detail || "no further detail."}</p>}
      {job.error && <p className="mt-1 text-rose-700">{job.error}</p>}
      <ol className="mt-2 space-y-1">
        {(job.stages || []).map((s) => {
          const icon = STAGE_ICON[s.status] || STAGE_ICON.pending;
          return (
            <li key={s.name} className="flex items-center gap-2">
              <span className={`w-4 text-center ${icon.cls}`}>{icon.glyph}</span>
              <span>{stageLabel(s.name, lang)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded border border-gray-200 bg-gray-50 px-2 py-1">
      <div className="text-sm font-semibold text-gray-900">{value ?? "—"}</div>
      <div className="text-[10px] text-gray-500">{label}</div>
    </div>
  );
}

function DocumentResult({ result, jobId, lang }) {
  const parcels = result.linked_parcels || [];
  return (
    <div className="space-y-2 text-xs" data-testid="document-result">
      <p className="font-medium text-gray-600">{it("result_title", lang)}</p>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Stat label="Document type" value={result.doc_type} />
        <Stat label="Pages" value={result.pages} />
        <Stat label="Rows extracted" value={result.extraction_rows} />
        <Stat label="Facts" value={result.facts} />
      </div>
      {parcels.length > 0 && (
        <div>
          <span className="text-gray-500">{it("linked_parcels", lang)}: </span>
          {parcels.map((uid, i) => (
            <span key={uid}>
              {i > 0 && ", "}
              <Link to={`/parcel/${encodeURIComponent(uid)}`} className="text-emerald-700 underline">
                {uid}
              </Link>
            </span>
          ))}
        </div>
      )}
      {!!result.new_findings && (
        <p>
          <Link to="/findings" className="text-emerald-700 underline">
            +{result.new_findings} {it("new_findings", lang)}
          </Link>
        </p>
      )}
      {!!result.review_items && (
        <p>
          <Link to="/review" className="text-emerald-700 underline">
            {result.review_items} {it("review_items", lang)}
          </Link>
        </p>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <a
          href={`${API_BASE}/ingest/jobs/${jobId}/report?fmt=pdf`}
          className="rounded bg-emerald-600 px-2.5 py-1 font-medium text-white"
          data-testid="download-report-pdf"
        >
          {it("download_report", lang)}
        </a>
        <a
          href={`${API_BASE}/ingest/jobs/${jobId}/report?fmt=csv`}
          className="rounded border border-emerald-600 px-2.5 py-1 font-medium text-emerald-700"
          data-testid="download-report-csv"
        >
          {it("download_rows", lang)}
        </a>
      </div>
    </div>
  );
}

function SatelliteResult({ result, lang }) {
  return (
    <div className="space-y-2 text-xs" data-testid="satellite-result">
      <p className="font-medium text-gray-600">{it("result_title", lang)}</p>
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Stat label={it("new_scenes", lang)} value={result.new_scenes} />
        <Stat label={it("latest_scene", lang)} value={result.latest_scene_date} />
        <Stat label={it("parcels_updated", lang)} value={result.parcels_updated} />
        <Stat label="Seasons updated" value={result.seasons_updated} />
      </div>
      {!!result.new_findings && (
        <p>
          <Link to="/findings" className="text-emerald-700 underline">
            +{result.new_findings} {it("new_findings", lang)}
          </Link>
        </p>
      )}
    </div>
  );
}
