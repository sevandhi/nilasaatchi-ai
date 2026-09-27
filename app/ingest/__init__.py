"""New-document / satellite-refresh ingest path (D-062+ follow-up: "make the app handle NEW data").

A user-uploaded PDF runs through the same pipeline stages as the batch corpus (pipeline.catalog,
pipeline.classify, pipeline.extract, pipeline.load, pipeline.match, pipeline.findings) instead of a
reimplementation. Every stage after `store` runs in its own `uv run` subprocess (see
`app.ingest.stage_cli`) so an OCR/paddle crash fails only that stage, never the API process.

Modules:
  db.py            — a plain psycopg connection helper (this package's own processes/subprocesses;
                     the FastAPI app itself keeps using app.api.db's pooled connection).
  store.py         — `ingest_job` CRUD + stage-array bookkeeping (db/migrations/0015_ingest_job.sql).
  document_stages.py — one function per document stage (store/catalog/classify/extract/load/match/findings).
  satellite_stages.py — the satellite-refresh job (subprocess `planet.refresh`, then findings).
  stage_cli.py     — `uv run python -m app.ingest.stage_cli <kind> <stage> --job-id <id>`.
  orchestrator.py  — background-thread sequencing, called from app/api/routers/ingest.py.
"""
