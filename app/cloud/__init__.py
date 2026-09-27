"""Read-only cloud demo of the NilaSaatchi AI API (Phase 7 / T7.1, D-067, D-027/D-028).

Serves the same GET paths and JSON shapes as `app.api` from a pre-built snapshot (local dir or
`s3://` in AWS mode) instead of Postgres, so it can run on Lambda behind API Gateway without
RDS/EC2 (the account's AWS mode forbids both). Every mutating verb returns 405. See
`app/cloud/ENDPOINTS.md` for the endpoint-by-endpoint coverage table and
`scripts/cloud_export.py` for how the snapshot is built.

Deliberately does not import `app.api` (which pulls psycopg/pyarrow/etc., too heavy for a Lambda
zip) — response shapes are matched by hand against the live routers instead.
"""
