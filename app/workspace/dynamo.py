"""DynamoDB adapter for workspaces — AWS mode only (D-027/D-028; plan.md §6, §8 cut list).

Same interface as `app.workspace.store.WorkspaceStore` (create/get/get_version/list_versions/list/
update/delete) so `app/api/routers/workspaces.py` can swap backends via
`WORKSPACE_BACKEND=dynamodb` without touching route code. **Not wired in by default and never
auto-deployed**: creating the table (`fai-tce-team49-workspaces`, region `ap-south-1`, on-demand
billing) requires the user's explicit approval per CLAUDE.md/plan.md §6, and is a P7 stretch item
behind the AWS serverless deploy. `runs` follow the same pattern in `fai-tce-team49-runs`
(not implemented here; add alongside when P7 AWS deploy is approved).

Item shape mirrors the Postgres row: {id, name, version, spec (JSON string), ledger_head,
created_by, created_at, updated_at}. Versions are stored as `{id}#v{version}` sort-adjacent items
in the same table (single-table design) to avoid a second table under the no-RDS/no-EC2 constraint.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from app.workspace.models import WorkspaceCreate, WorkspaceRecord, WorkspaceSpec, WorkspaceUpdate
from app.workspace.store import WorkspaceNotFound

TABLE_NAME_TMPL = "fai-tce-{team}-workspaces"  # team e.g. "team49" (AWS_TEAM env)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _item_to_record(item: dict) -> WorkspaceRecord:
    spec = json.loads(item["spec"])
    return WorkspaceRecord(
        id=item["id"], name=item["name"], version=int(item["version"]), spec=WorkspaceSpec(**spec),
        ledger_head=item.get("ledger_head"), created_by=item.get("created_by"),
        created_at=item["created_at"], updated_at=item["updated_at"],
    )


class DynamoWorkspaceStore:
    """Region `ap-south-1` only; table name `fai-tce-team49-workspaces` (D-027/D-028)."""

    def __init__(self, table_name: str | None = None, region_name: str = "ap-south-1", resource=None) -> None:
        import boto3

        self.region_name = region_name
        self.table_name = table_name or TABLE_NAME_TMPL.format(team="team49")
        self._resource = resource or boto3.resource("dynamodb", region_name=region_name)
        self.table = self._resource.Table(self.table_name)

    def create(self, payload: WorkspaceCreate) -> WorkspaceRecord:
        wid = uuid.uuid4().hex
        now = _now()
        spec_json = json.dumps(payload.spec.model_dump(mode="json"))
        item = {"id": wid, "sk": "latest", "name": payload.name, "version": 1, "spec": spec_json,
                "ledger_head": payload.spec.ledger_head, "created_by": payload.created_by,
                "created_at": now, "updated_at": now}
        self.table.put_item(Item=item)
        self.table.put_item(Item={**item, "sk": "v1"})
        return _item_to_record(item)

    def get(self, workspace_id: str) -> WorkspaceRecord:
        resp = self.table.get_item(Key={"id": workspace_id, "sk": "latest"})
        item = resp.get("Item")
        if item is None:
            raise WorkspaceNotFound(workspace_id)
        return _item_to_record(item)

    def get_version(self, workspace_id: str, version: int) -> WorkspaceRecord:
        resp = self.table.get_item(Key={"id": workspace_id, "sk": f"v{version}"})
        item = resp.get("Item")
        if item is None:
            raise WorkspaceNotFound(f"{workspace_id}@v{version}")
        return _item_to_record(item)

    def list_versions(self, workspace_id: str) -> list[int]:
        resp = self.table.query(
            KeyConditionExpression="id = :id AND begins_with(sk, :v)",
            ExpressionAttributeValues={":id": workspace_id, ":v": "v"},
        )
        items = resp.get("Items", [])
        if not items:
            raise WorkspaceNotFound(workspace_id)
        return sorted(int(i["sk"][1:]) for i in items)

    def list(self, limit: int = 50, offset: int = 0) -> list[WorkspaceRecord]:
        # Demo-scale scan (workspaces are few); a GSI on updated_at would replace this at real scale.
        resp = self.table.scan(FilterExpression="sk = :latest", ExpressionAttributeValues={":latest": "latest"})
        items = sorted(resp.get("Items", []), key=lambda i: i["updated_at"], reverse=True)
        return [_item_to_record(i) for i in items[offset : offset + limit]]

    def update(self, workspace_id: str, payload: WorkspaceUpdate) -> WorkspaceRecord:
        current = self.get(workspace_id)
        new_version = current.version + 1
        now = _now()
        spec_json = json.dumps(payload.spec.model_dump(mode="json"))
        item = {"id": workspace_id, "sk": "latest", "name": payload.name or current.name,
                "version": new_version, "spec": spec_json, "ledger_head": payload.spec.ledger_head,
                "created_by": current.created_by, "created_at": current.created_at.isoformat(),
                "updated_at": now}
        self.table.put_item(Item=item)
        self.table.put_item(Item={**item, "sk": f"v{new_version}"})
        return _item_to_record(item)

    def delete(self, workspace_id: str) -> None:
        versions = self.list_versions(workspace_id)  # raises WorkspaceNotFound
        with self.table.batch_writer() as batch:
            batch.delete_item(Key={"id": workspace_id, "sk": "latest"})
            for v in versions:
                batch.delete_item(Key={"id": workspace_id, "sk": f"v{v}"})
