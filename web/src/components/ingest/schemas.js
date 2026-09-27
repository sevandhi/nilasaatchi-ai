// Local, deliberately loose zod schemas for the ingest endpoints (upload + satellite refresh).
// This contract is still being built by the backend agent, so every field that isn't load-bearing
// for the UI is optional/nullable — a shape drift here should degrade to "unvalidated JSON" (see
// apiFetch in ../../api/client.js) rather than break the page. Do not add these to
// ../../api/schemas.js — that file is generated from the live OpenAPI doc by another agent.
import { z } from "zod";

const idLike = z.union([z.string(), z.number()]);

export const IngestStageSchema = z.object({
  name: z.string(),
  status: z.enum(["pending", "running", "done", "skipped", "failed"]).catch("pending"),
  started_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
});

export const IngestJobSchema = z
  .object({
    id: idLike,
    kind: z.string().optional(),
    status: z.enum(["queued", "running", "done", "failed", "duplicate"]).catch("queued"),
    filename: z.string().nullable().optional(),
    created_at: z.string().nullable().optional(),
    started_at: z.string().nullable().optional(),
    finished_at: z.string().nullable().optional(),
    document_id: idLike.nullable().optional(),
    duplicate_of: idLike.nullable().optional(),
    stages: z.array(IngestStageSchema).optional().default([]),
    result: z.record(z.any()).nullable().optional(),
    error: z.string().nullable().optional(),
  })
  .passthrough();

export const IngestJobListResponseSchema = z
  .object({
    jobs: z.array(IngestJobSchema).optional().default([]),
  })
  .passthrough();

export const UploadDocumentResponseSchema = z
  .object({
    job_id: idLike,
    status: z.string().optional(),
    duplicate_of: idLike.nullable().optional(),
  })
  .passthrough();

export const SatelliteRefreshResponseSchema = z
  .object({
    job_id: idLike.optional(),
    running_job_id: idLike.nullable().optional(),
  })
  .passthrough();

export const SatelliteStatusResponseSchema = z
  .object({
    latest_scene_date: z.string().nullable().optional(),
    scene_count: z.number().nullable().optional(),
    last_refresh_at: z.string().nullable().optional(),
    running_job_id: idLike.nullable().optional(),
  })
  .passthrough();
