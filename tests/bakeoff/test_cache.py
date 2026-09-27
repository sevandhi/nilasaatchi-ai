from spikes.ocr_bakeoff.cache import ContentCache, content_hash


def test_content_hash_stable_and_sensitive():
    h1 = content_hash(b"page-bytes", "candidate-a", "psm=6")
    h2 = content_hash(b"page-bytes", "candidate-a", "psm=6")
    h3 = content_hash(b"page-bytes", "candidate-a", "psm=7")
    assert h1 == h2
    assert h1 != h3


def test_cache_get_set_roundtrip(tmp_path):
    cache = ContentCache(tmp_path)
    key = content_hash(b"x", "a")
    assert cache.get(key) is None
    cache.set(key, {"text": "hello", "seconds": 1.2})
    got = cache.get(key)
    assert got["text"] == "hello"
    assert got["seconds"] == 1.2


def test_get_or_compute_only_computes_once(tmp_path):
    cache = ContentCache(tmp_path)
    key = content_hash(b"y", "b")
    calls = []

    def compute():
        calls.append(1)
        return {"text": "computed"}

    first = cache.get_or_compute(key, compute)
    second = cache.get_or_compute(key, compute)
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["text"] == "computed"
    assert len(calls) == 1  # idempotent: compute() only ran once


def test_cache_is_a_real_file_on_disk(tmp_path):
    cache = ContentCache(tmp_path)
    key = content_hash(b"z", "c")
    cache.set(key, {"ok": True})
    assert cache.path_for(key).exists()
