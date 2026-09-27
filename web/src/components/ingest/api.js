// Thin wrapper around the ingest endpoints. Reuses the shared envelope shape from
// ../../api/client.js (ok/kind/message) so callers can treat these exactly like the
// `useApi`-backed calls elsewhere, but adds the one thing apiFetch can't do: a multipart
// (file) POST for the document upload, and a 409 ("a refresh is already running") case for
// the satellite refresh.
import { API_BASE, apiGet } from "../../api/client.js";
import {
  IngestJobListResponseSchema,
  IngestJobSchema,
  SatelliteRefreshResponseSchema,
  SatelliteStatusResponseSchema,
  UploadDocumentResponseSchema,
} from "./schemas.js";

async function toEnvelope(res, schema) {
  let json = null;
  const text = await res.text();
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  if (!res.ok) {
    if (res.status === 404 && (!json || json.detail === "Not Found")) {
      return { ok: false, kind: "not_implemented", message: "Upload service not available.", status: 404 };
    }
    if (res.status === 409) {
      return { ok: false, kind: "conflict", message: "A refresh is already running.", status: 409, data: json };
    }
    const message = json?.detail ? (typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail)) : res.statusText;
    return { ok: false, kind: "error", message: message || `Request failed (${res.status})`, status: res.status };
  }
  if (schema) {
    const parsed = schema.safeParse(json);
    if (!parsed.success) {
      console.warn("ingest response did not match the local schema", parsed.error.issues);
      return { ok: true, data: json, validated: false };
    }
    return { ok: true, data: parsed.data, validated: true };
  }
  return { ok: true, data: json, validated: false };
}

/** POST /ingest/documents (multipart: file + optional village + optional declared doc_type). */
export async function uploadDocument({ file, village, docType, signal }) {
  const form = new FormData();
  form.append("file", file);
  if (village) form.append("village", village);
  if (docType) form.append("doc_type", docType);
  let res;
  try {
    res = await fetch(`${API_BASE}/ingest/documents`, { method: "POST", body: form, signal });
  } catch (e) {
    if (e?.name === "AbortError") throw e;
    return { ok: false, kind: "network", message: "Cannot reach the server. Please check that the backend is running and try again." };
  }
  return toEnvelope(res, UploadDocumentResponseSchema);
}

/** GET /ingest/jobs?kind=&limit= */
export function listIngestJobs(kind, limit = 20, signal) {
  return apiGet("/ingest/jobs", { kind, limit }, IngestJobListResponseSchema, signal).then((res) =>
    res.ok ? res : normalizeNotImplemented(res)
  );
}

/** GET /ingest/jobs/{id} */
export function getIngestJob(id, signal) {
  return apiGet(`/ingest/jobs/${id}`, undefined, IngestJobSchema, signal).then((res) => (res.ok ? res : normalizeNotImplemented(res)));
}

/** POST /ingest/satellite-refresh {max_scenes?} — surfaces 409 as kind:"conflict". */
export async function refreshSatellite(maxScenes) {
  let res;
  try {
    res = await fetch(`${API_BASE}/ingest/satellite-refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(maxScenes ? { max_scenes: maxScenes } : {}),
    });
  } catch {
    return { ok: false, kind: "network", message: "Cannot reach the server. Please check that the backend is running and try again." };
  }
  return toEnvelope(res, SatelliteRefreshResponseSchema);
}

/** GET /ingest/satellite/status */
export function getSatelliteStatus(signal) {
  return apiGet("/ingest/satellite/status", undefined, SatelliteStatusResponseSchema, signal).then((res) =>
    res.ok ? res : normalizeNotImplemented(res)
  );
}

// apiGet's generic client returns kind:"not_found" for any non-404 error status too — rename to
// "error" for anything that isn't actually a 404, so widgets don't call a 500 "not found".
function normalizeNotImplemented(res) {
  if (res.kind === "not_found" && res.status && res.status !== 404) return { ...res, kind: "error" };
  return res;
}
