import { useRef, useState } from "react";
import { useStore } from "../../store/useStore.js";
import { it } from "./labels.js";
import { uploadDocument } from "./api.js";
import { JobProgress } from "./JobProgress.jsx";

const MAX_BYTES = 50 * 1024 * 1024;

// plain names for the document types the table reader handles (values match GET /ingest/doc-types)
const DOC_TYPES = [
  ["AWARD_7_2", "Award 7(2)"], ["AWARD_7_3", "Award 7(3)"], ["FORM_F", "Form F"], ["FORM_E", "Form E"],
  ["CHITTA", "Chitta / patta extract"], ["LDR", "Land delivery receipt (LDR)"], ["POSSESSION_CERT", "Possession certificate"],
  ["DISBURSEMENT", "Amount disbursed"], ["COURT_DEPOSIT", "Court deposit"], ["BANK_INSTRUMENT", "Bank instrument"],
  ["SEC32_NOTICE", "3(2) notice"], ["SEC32_ERRATA", "3(2) errata"], ["LPS", "Land plan schedule (LPS)"],
  ["GO", "Government order (GO)"], ["AS", "Administrative sanction (AS)"], ["AS_PROPOSAL", "AS proposal"],
];

/**
 * Drag-and-drop (or choose) a land-acquisition PDF, an optional village, and a submit button.
 * On success it shows a live stage stepper (via JobProgress) until the pipeline finishes.
 * @param {{onJobCreated?: (jobId: string|number) => void, onJobSettled?: (job: object) => void}} props
 */
export function UploadPanel({ onJobCreated, onJobSettled }) {
  const lang = useStore((s) => s.lang);
  const [file, setFile] = useState(null);
  const [village, setVillage] = useState("");
  const [docType, setDocType] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [validationError, setValidationError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [jobId, setJobId] = useState(null);
  const inputRef = useRef(null);

  function validate(f) {
    if (!f) return null;
    if (!/\.pdf$/i.test(f.name) || (f.type && f.type !== "application/pdf")) {
      return it("upload_invalid_type", lang);
    }
    if (f.size > MAX_BYTES) return it("upload_too_large", lang);
    return null;
  }

  function pickFile(f) {
    const err = validate(f);
    setValidationError(err);
    setFile(err ? null : f);
  }

  async function submit(e) {
    e.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setSubmitError(null);
    setJobId(null);
    const res = await uploadDocument({ file, village: village.trim() || undefined, docType: docType || undefined });
    setSubmitting(false);
    if (!res.ok) {
      setSubmitError(res.kind === "not_implemented" ? it("upload_service_unavailable", lang) : res.message);
      return;
    }
    setJobId(res.data.job_id);
    onJobCreated?.(res.data.job_id);
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3" data-testid="upload-panel">
      <h2 className="mb-2 text-sm font-semibold text-gray-600">{it("upload_title", lang)}</h2>
      <form onSubmit={submit} className="space-y-2">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) pickFile(f);
          }}
          className={`flex flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed p-4 text-xs text-gray-500 ${
            dragOver ? "border-emerald-400 bg-emerald-50" : "border-gray-300"
          }`}
          data-testid="upload-dropzone"
        >
          {file ? (
            <span className="font-medium text-gray-700" data-testid="upload-filename">{file.name} ({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
          ) : (
            <>
              <span>{it("upload_drop", lang)}</span>
              <button type="button" onClick={() => inputRef.current?.click()} className="text-emerald-700 underline">
                {it("upload_choose", lang)}
              </button>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            data-testid="upload-input"
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
        </div>
        {validationError && <p className="text-xs text-rose-600" data-testid="upload-validation-error">{validationError}</p>}

        <label className="flex items-center gap-2 text-xs text-gray-600">
          {it("upload_village", lang)}
          <input
            value={village}
            onChange={(e) => setVillage(e.target.value)}
            className="w-48 rounded border border-gray-300 px-2 py-1"
            data-testid="upload-village"
          />
        </label>

        <label className="flex items-center gap-2 text-xs text-gray-600">
          {it("upload_doc_type", lang)}
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            className="w-56 rounded border border-gray-300 px-2 py-1"
            data-testid="upload-doc-type"
          >
            <option value="">{it("upload_doc_type_auto", lang)}</option>
            {DOC_TYPES.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>

        <button
          type="submit"
          disabled={!file || submitting}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
          data-testid="upload-submit"
        >
          {submitting ? it("upload_uploading", lang) : it("upload_submit", lang)}
        </button>
        {submitError && <p className="text-xs text-rose-600" data-testid="upload-submit-error">{submitError}</p>}
      </form>

      <p className="mt-2 text-[11px] text-gray-400">{it("upload_honesty", lang)}</p>

      {jobId && (
        <div className="mt-3">
          <JobProgress jobId={jobId} kind="document" onSettled={onJobSettled} />
        </div>
      )}
    </div>
  );
}
