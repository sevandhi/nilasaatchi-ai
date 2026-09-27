"""Offline tests for infra/bedrock_batch (upload/status/download key layout + worker handler logic).

No live AWS calls: S3 and bedrock-runtime are replaced with in-memory fakes (tests/infra/fakes.py).
"""
from __future__ import annotations

import base64
import json

from infra.bedrock_batch import (
    batch_prefix,
    done_key,
    img_key,
    requests_key,
    responses_key,
)
from infra.bedrock_batch import download as download_mod
from infra.bedrock_batch import status as status_mod
from infra.bedrock_batch import upload as upload_mod
from infra.bedrock_batch.worker import handler as h
from tests.infra.fakes import FakeBedrock, FakeS3, client_error, converse_ok

BID = "b-test-infra-001"


# ---- key layout -------------------------------------------------------------------------------
def test_key_layout():
    assert batch_prefix(BID) == f"bedrock-batch/{BID}"
    assert img_key(BID, "img/abc.jpg") == f"bedrock-batch/{BID}/img/abc.jpg"
    assert requests_key(BID, "part-0000") == f"bedrock-batch/{BID}/parts/part-0000.requests.jsonl"
    assert responses_key(BID, "part-0000") == f"bedrock-batch/{BID}/parts/part-0000.responses.jsonl"
    assert done_key(BID, "part-0000") == f"bedrock-batch/{BID}/parts/part-0000.done"


def test_requests_key_regex_only_matches_request_parts():
    ok = f"bedrock-batch/{BID}/parts/part-0000.requests.jsonl"
    assert h.REQUESTS_KEY_RE.match(ok).group("batch_id") == BID
    assert h.REQUESTS_KEY_RE.match(ok).group("part") == "part-0000"
    # the worker's own outputs must NOT re-match (else it would re-trigger itself)
    assert h.REQUESTS_KEY_RE.match(f"bedrock-batch/{BID}/parts/part-0000.responses.jsonl") is None
    assert h.REQUESTS_KEY_RE.match(f"bedrock-batch/{BID}/parts/part-0000.done") is None
    assert h.REQUESTS_KEY_RE.match(f"bedrock-batch/{BID}/img/abc.jpg") is None


# ---- upload -------------------------------------------------------------------------------------
def _write_local_batch(tmp_path, n_requests: int, n_images: int = 0, monkeypatch=None):
    bdir = tmp_path / "data" / "bedrock_batch" / BID
    bdir.mkdir(parents=True)
    lines = [json.dumps({"key": f"k{i}", "op": "converse"}) for i in range(n_requests)]
    (bdir / "requests.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if n_images:
        (bdir / "img").mkdir()
        for i in range(n_images):
            (bdir / "img" / f"img{i}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([i]))
    monkeypatch.setenv("ROUTER_DATA_DIR", str(tmp_path / "data"))
    return bdir


def test_upload_splits_into_parts_of_50(tmp_path, monkeypatch):
    _write_local_batch(tmp_path, n_requests=120, monkeypatch=monkeypatch)
    client = FakeS3()
    manifest = upload_mod.upload_batch(BID, client=client)
    assert manifest["parts"] == ["part-0000", "part-0001", "part-0002"]
    assert manifest["n_requests"] == 120
    part0 = client.objects[requests_key(BID, "part-0000")].decode().splitlines()
    part2 = client.objects[requests_key(BID, "part-0002")].decode().splitlines()
    assert len(part0) == 50 and len(part2) == 20


def test_upload_images_before_request_parts(tmp_path, monkeypatch):
    _write_local_batch(tmp_path, n_requests=5, n_images=2, monkeypatch=monkeypatch)
    client = FakeS3()
    manifest = upload_mod.upload_batch(BID, client=client)
    assert manifest["n_images_uploaded"] == 2
    img_calls = [k for k in client.put_calls if "/img/" in k]
    part_calls = [k for k in client.put_calls if "/parts/" in k]
    assert img_calls and part_calls
    assert client.put_calls.index(img_calls[-1]) < client.put_calls.index(part_calls[0])


def test_upload_skips_already_uploaded_images(tmp_path, monkeypatch):
    _write_local_batch(tmp_path, n_requests=1, n_images=2, monkeypatch=monkeypatch)
    client = FakeS3()
    client.objects[img_key(BID, "img/img0.jpg")] = b"already-there"
    manifest = upload_mod.upload_batch(BID, client=client)
    assert manifest["n_images_uploaded"] == 1          # only img1 was new


def test_upload_dry_run_makes_no_aws_calls(tmp_path, monkeypatch):
    _write_local_batch(tmp_path, n_requests=10, n_images=1, monkeypatch=monkeypatch)

    def _boom():
        raise AssertionError("s3_client() must not be called in --dry-run")

    monkeypatch.setattr(upload_mod, "s3_client", _boom)
    manifest = upload_mod.upload_batch(BID, dry_run=True)
    assert manifest["parts"] == ["part-0000"] and manifest["n_images_uploaded"] == 1
    assert not (upload_mod.local_batch_dir(BID) / "upload_manifest.json").exists()


def test_upload_writes_manifest_when_not_dry_run(tmp_path, monkeypatch):
    bdir = _write_local_batch(tmp_path, n_requests=3, monkeypatch=monkeypatch)
    upload_mod.upload_batch(BID, client=FakeS3())
    assert json.loads((bdir / "upload_manifest.json").read_text())["n_requests"] == 3


# ---- status / download ---------------------------------------------------------------------------
def _seed_uploaded_batch(client: FakeS3, n_parts: int):
    for i in range(n_parts):
        part = f"part-{i:04d}"
        client.objects[requests_key(BID, part)] = b"{}\n"


def test_status_reports_pending_parts_before_done_markers():
    client = FakeS3()
    _seed_uploaded_batch(client, 2)
    s = status_mod.batch_status(BID, client=client)
    assert s["parts_total"] == 2 and s["parts_done"] == 0
    assert s["parts_pending"] == ["part-0000", "part-0001"]


def test_status_tallies_ok_and_error_rows():
    client = FakeS3()
    _seed_uploaded_batch(client, 1)
    rows = [{"key": "k1", "ok": True}, {"key": "k2", "ok": False, "error": "ThrottlingException"}]
    client.objects[responses_key(BID, "part-0000")] = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    client.objects[done_key(BID, "part-0000")] = b"{}"
    s = status_mod.batch_status(BID, client=client)
    assert (s["parts_total"], s["parts_done"], s["responses_ok"], s["responses_error"]) == (1, 1, 1, 1)
    assert s["parts_pending"] == []
    assert "k2: ThrottlingException" in s["error_samples"]


def test_download_writes_local_response_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_DATA_DIR", str(tmp_path / "data"))
    client = FakeS3()
    client.objects[responses_key(BID, "part-0000")] = b'{"key":"k1","ok":true}\n'
    client.objects[responses_key(BID, "part-0001")] = b'{"key":"k2","ok":false}\n'
    out = download_mod.download_batch(BID, client=client)
    assert out["responses_downloaded"] == 2
    d = download_mod.local_batch_dir(BID) / "responses"
    assert json.loads((d / "part-0000.jsonl").read_text())["key"] == "k1"
    assert json.loads((d / "part-0001.jsonl").read_text())["key"] == "k2"


# ---- worker handler: dispatch + allow-list -------------------------------------------------------
def _fake_context(remaining_ms=600_000):
    class Ctx:
        def get_remaining_time_in_millis(self):
            return remaining_ms
    return Ctx()


def test_converse_rejects_model_outside_allow_list(monkeypatch):
    client = FakeS3()
    req = {"key": "k1", "op": "converse", "model_id": "some-other-model",
           "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    row = h._process_converse("bucket", BID, req, lambda: 600_000)
    assert row == {"key": "k1", "ok": False, "output_text": None, "usage": {}, "latency_ms": 0,
                   "error": "model_not_allowed:some-other-model"}


def test_converse_ok_loads_image_from_s3(monkeypatch):
    client = FakeS3()
    client.objects[f"bedrock-batch/{BID}/img/abc.jpg"] = b"\xff\xd8\xff-fake-jpeg"
    bedrock = FakeBedrock(converse_script=[converse_ok("OK", tokens_in=7, tokens_out=1)])
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-3b-instruct", "system": "sys prompt",
           "messages": [{"role": "user", "content": [{"text": "read"}, {"image": {"format": "jpeg", "s3_key": "img/abc.jpg"}}]}],
           "inferenceConfig": {"maxTokens": 5, "temperature": 0}}
    row = h._process_converse("bucket", BID, req, lambda: 600_000)
    assert row["ok"] and row["output_text"] == "OK"
    assert row["usage"] == {"inputTokens": 7, "outputTokens": 1}
    sent = bedrock.converse_calls[0]
    assert sent["system"] == [{"text": "sys prompt"}]
    assert sent["messages"][0]["content"][1]["image"]["source"]["bytes"] == b"\xff\xd8\xff-fake-jpeg"


def test_converse_retries_throttle_then_succeeds(monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)          # no real waiting in tests
    bedrock = FakeBedrock(converse_script=[client_error("ThrottlingException"), converse_ok("done")])
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-8b-instruct",
           "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    row = h._process_converse("bucket", BID, req, lambda: 600_000)
    assert row["ok"] and row["output_text"] == "done"
    assert len(bedrock.converse_calls) == 2


def test_converse_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    bedrock = FakeBedrock(converse_script=[client_error("ThrottlingException")] * h.MAX_ATTEMPTS)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-8b-instruct",
           "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    row = h._process_converse("bucket", BID, req, lambda: 600_000)
    assert not row["ok"] and row["error"] == "ThrottlingException"
    assert len(bedrock.converse_calls) == h.MAX_ATTEMPTS


def test_converse_nonretryable_error_fails_fast(monkeypatch):
    bedrock = FakeBedrock(converse_script=[client_error("ValidationException")])
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-8b-instruct",
           "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    row = h._process_converse("bucket", BID, req, lambda: 600_000)
    assert not row["ok"] and row["error"] == "ValidationException"
    assert len(bedrock.converse_calls) == 1


def test_embed_text_ok(monkeypatch):
    bedrock = FakeBedrock(invoke_script=[{"embedding": [0.1, 0.2], "inputTextTokenCount": 4}])
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "embed_text", "model_id": "amazon.titan-embed-text-v2:0",
           "inputText": "survey 233/2", "dimensions": 1024, "normalize": True}
    row = h._process_embed_text(req, lambda: 600_000)
    assert row["ok"] and row["embedding"] == [0.1, 0.2] and row["usage"]["inputTokens"] == 4
    body = json.loads(bedrock.invoke_calls[0]["body"])
    assert body == {"inputText": "survey 233/2", "dimensions": 1024, "normalize": True}


def test_embed_text_rejects_model_outside_allow_list(monkeypatch):
    req = {"key": "k1", "op": "embed_text", "model_id": "not-titan", "inputText": "x"}
    row = h._process_embed_text(req, lambda: 600_000)
    assert row == {"key": "k1", "ok": False, "usage": {}, "latency_ms": 0, "error": "model_not_allowed:not-titan"}


def test_embed_image_ok(monkeypatch):
    client = FakeS3()
    client.objects[f"bedrock-batch/{BID}/img/xyz.jpg"] = b"png-bytes"
    bedrock = FakeBedrock(invoke_script=[{"embedding": [0.5] * 4}])
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    req = {"key": "k1", "op": "embed_image", "model_id": "amazon.titan-embed-image-v1",
           "image": {"format": "jpeg", "s3_key": "img/xyz.jpg"}, "dimensions": 4}
    row = h._process_embed_image("bucket", BID, req, lambda: 600_000)
    assert row["ok"] and row["embedding"] == [0.5] * 4
    body = json.loads(bedrock.invoke_calls[0]["body"])
    assert base64.b64decode(body["inputImage"]) == b"png-bytes"
    assert body["embeddingConfig"] == {"outputEmbeddingLength": 4}


def test_process_part_end_to_end_writes_responses_and_done(monkeypatch):
    client = FakeS3()
    req1 = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-3b-instruct",
            "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    req2 = {"key": "k2", "op": "unknown_op"}
    client.objects[requests_key(BID, "part-0000")] = ("\n".join(json.dumps(r) for r in [req1, req2]) + "\n").encode()
    bedrock = FakeBedrock(converse_script=[converse_ok("hello")])
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    summary = h._process_part("bucket", BID, "part-0000", context=_fake_context())
    assert summary == {"batch_id": BID, "part": "part-0000", "requests": 2, "ok": 1, "errors": 1}
    assert done_key(BID, "part-0000") in client.objects
    rows = [json.loads(x) for x in client.objects[responses_key(BID, "part-0000")].decode().splitlines()]
    assert rows[0]["ok"] and rows[1] == {"key": "k2", "ok": False, "usage": {}, "latency_ms": 0,
                                          "error": "unknown_op:unknown_op"}


def test_process_part_time_budget_exceeded_marks_remaining_as_error(monkeypatch):
    client = FakeS3()
    req = {"key": "k1", "op": "converse", "model_id": "mistral.ministral-3-3b-instruct",
           "messages": [{"role": "user", "content": [{"text": "hi"}]}], "inferenceConfig": {"maxTokens": 5}}
    client.objects[requests_key(BID, "part-0000")] = json.dumps(req).encode() + b"\n"
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    summary = h._process_part("bucket", BID, "part-0000", context=_fake_context(remaining_ms=1000))
    rows = [json.loads(x) for x in client.objects[responses_key(BID, "part-0000")].decode().splitlines()]
    assert rows == [{"key": "k1", "ok": False, "usage": {}, "latency_ms": 0, "error": "lambda_time_budget_exceeded"}]
    assert summary["errors"] == 1


def test_handler_s3_event_dispatches_to_matching_key_only(monkeypatch):
    client = FakeS3()
    client.objects[requests_key(BID, "part-0000")] = b'{"key":"k1","op":"converse","model_id":"bad-model"}\n'
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    event = {"Records": [
        {"s3": {"bucket": {"name": "bucket"}, "object": {"key": requests_key(BID, "part-0000")}}},
        {"s3": {"bucket": {"name": "bucket"}, "object": {"key": responses_key(BID, "part-0000")}}},  # must be ignored
    ]}
    out = h.handler(event, context=_fake_context())
    assert len(out["processed"]) == 1 and out["processed"][0]["part"] == "part-0000"


def test_self_test_reports_s3_and_bedrock_status(monkeypatch):
    client = FakeS3()
    bedrock = FakeBedrock(converse_script=[converse_ok("OK", tokens_in=3, tokens_out=1)])
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    result = h._self_test(_fake_context())
    latency_ms = result.pop("latency_ms")
    assert isinstance(latency_ms, int) and latency_ms >= 0
    assert result == {"s3_ok": True, "bedrock_ok": True, "model_id": "mistral.ministral-3-3b-instruct",
                       "output_text": "OK", "usage": {"inputTokens": 3, "outputTokens": 1}}


def test_self_test_reports_bedrock_access_denied(monkeypatch):
    client = FakeS3()
    bedrock = FakeBedrock(converse_script=[client_error("AccessDeniedException", op="Converse")])
    monkeypatch.setattr(h, "_s3_client", lambda: client)
    monkeypatch.setattr(h, "_bedrock_client", lambda: bedrock)
    result = h._self_test(_fake_context())
    assert result["s3_ok"] and not result["bedrock_ok"]
    assert result["error"] == "ClientError" and "AccessDeniedException" in result["error_detail"]
