"""In-memory fakes for boto3 S3 / bedrock-runtime clients used by tests/infra (no moto dependency)."""
from __future__ import annotations

import json


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3:
    """A tiny in-memory stand-in for a boto3 S3 client, enough for upload/status/download/handler tests."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    # -- object CRUD ---------------------------------------------------------------------------
    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode("utf-8")
        self.put_calls.append(Key)
        return {}

    def get_object(self, Bucket, Key):
        return {"Body": _Body(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}

    def upload_file(self, filename, Bucket, Key):
        with open(filename, "rb") as f:
            self.objects[Key] = f.read()
        self.put_calls.append(Key)

    def download_file(self, Bucket, Key, filename):
        with open(filename, "wb") as f:
            f.write(self.objects[Key])

    # -- listing --------------------------------------------------------------------------------
    def get_paginator(self, op):
        assert op == "list_objects_v2"
        return _Paginator(self)

    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if MaxKeys:
            keys = keys[:MaxKeys]
        return {"Contents": [{"Key": k} for k in keys], "KeyCount": len(keys)}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)
        return {}


class _Paginator:
    def __init__(self, client: FakeS3):
        self.client = client

    def paginate(self, Bucket, Prefix=""):
        yield self.client.list_objects_v2(Bucket, Prefix)


def client_error(code: str, op: str = "Converse", status: int = 400):
    """A real botocore.exceptions.ClientError, so handler._error_kind's isinstance check applies."""
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op)


class FakeBedrock:
    """Scripted bedrock-runtime client: `converse_script` / `invoke_script` are lists of either a
    result dict or an Exception instance, consumed in order (per call), so tests can simulate
    throttle-then-succeed sequences."""

    def __init__(self, converse_script=None, invoke_script=None):
        self.converse_script = list(converse_script or [])
        self.invoke_script = list(invoke_script or [])
        self.converse_calls: list[dict] = []
        self.invoke_calls: list[dict] = []

    def converse(self, **kwargs):
        self.converse_calls.append(kwargs)
        item = self.converse_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def invoke_model(self, **kwargs):
        self.invoke_calls.append(kwargs)
        item = self.invoke_script.pop(0)
        if isinstance(item, Exception):
            raise item
        body = json.dumps(item).encode("utf-8")
        return {"body": _Body(body)}


def converse_ok(text: str, tokens_in: int = 10, tokens_out: int = 2, latency_ms: int = 50) -> dict:
    return {"output": {"message": {"content": [{"text": text}]}},
            "usage": {"inputTokens": tokens_in, "outputTokens": tokens_out},
            "metrics": {"latencyMs": latency_ms}}
