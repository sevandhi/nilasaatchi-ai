"""Postgres-backed workspace CRUD + versioning (db/migrations/0011_workspace_run.sql).

`WorkspaceStore` is the only supported backend for the local/demo deployment. `DynamoWorkspaceStore`
(app/workspace/dynamo.py) implements the same interface for AWS mode (P7 stretch, approval-gated,
never wired in by default — see WORKSPACE_BACKEND in app/api/config.py).
"""
from __future__ import annotations

import json
import uuid

import psycopg

from app.workspace.models import WorkspaceCreate, WorkspaceRecord, WorkspaceSpec, WorkspaceUpdate


class WorkspaceNotFound(KeyError):
    pass


def _row_to_record(row: dict) -> WorkspaceRecord:
    spec = row["spec"]
    if isinstance(spec, str):
        spec = json.loads(spec)
    return WorkspaceRecord(
        id=row["id"], name=row["name"], version=row["version"], spec=WorkspaceSpec(**spec),
        ledger_head=row.get("ledger_head"), created_by=row.get("created_by"),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class WorkspaceStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def create(self, payload: WorkspaceCreate) -> WorkspaceRecord:
        wid = uuid.uuid4().hex
        spec_json = payload.spec.model_dump(mode="json")
        row = self.conn.execute(
            "INSERT INTO workspace (id, name, version, spec, ledger_head, created_by) "
            "VALUES (%s, %s, 1, %s, %s, %s) RETURNING *",
            (wid, payload.name, json.dumps(spec_json), payload.spec.ledger_head, payload.created_by),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO workspace_version (workspace_id, version, spec, ledger_head) VALUES (%s, 1, %s, %s)",
            (wid, json.dumps(spec_json), payload.spec.ledger_head),
        )
        self.conn.commit()
        return _row_to_record(row)

    def get(self, workspace_id: str) -> WorkspaceRecord:
        row = self.conn.execute("SELECT * FROM workspace WHERE id = %s", (workspace_id,)).fetchone()
        if row is None:
            raise WorkspaceNotFound(workspace_id)
        return _row_to_record(row)

    def get_version(self, workspace_id: str, version: int) -> WorkspaceRecord:
        row = self.conn.execute(
            "SELECT w.id, w.name, wv.version, wv.spec, wv.ledger_head, w.created_by, "
            "wv.created_at AS created_at, wv.created_at AS updated_at "
            "FROM workspace_version wv JOIN workspace w ON w.id = wv.workspace_id "
            "WHERE wv.workspace_id = %s AND wv.version = %s", (workspace_id, version),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"{workspace_id}@v{version}")
        return _row_to_record(row)

    def list_versions(self, workspace_id: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT version FROM workspace_version WHERE workspace_id = %s ORDER BY version",
            (workspace_id,),
        ).fetchall()
        if not rows:
            raise WorkspaceNotFound(workspace_id)
        return [r["version"] for r in rows]

    def list(self, limit: int = 50, offset: int = 0) -> list[WorkspaceRecord]:
        rows = self.conn.execute(
            "SELECT * FROM workspace ORDER BY updated_at DESC LIMIT %s OFFSET %s", (limit, offset),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def update(self, workspace_id: str, payload: WorkspaceUpdate) -> WorkspaceRecord:
        current = self.get(workspace_id)  # raises WorkspaceNotFound
        new_version = current.version + 1
        spec_json = payload.spec.model_dump(mode="json")
        name = payload.name or current.name
        row = self.conn.execute(
            "UPDATE workspace SET name = %s, version = %s, spec = %s, ledger_head = %s, updated_at = now() "
            "WHERE id = %s RETURNING *",
            (name, new_version, json.dumps(spec_json), payload.spec.ledger_head, workspace_id),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO workspace_version (workspace_id, version, spec, ledger_head) VALUES (%s, %s, %s, %s)",
            (workspace_id, new_version, json.dumps(spec_json), payload.spec.ledger_head),
        )
        self.conn.commit()
        return _row_to_record(row)

    def delete(self, workspace_id: str) -> None:
        cur = self.conn.execute("DELETE FROM workspace WHERE id = %s", (workspace_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise WorkspaceNotFound(workspace_id)
