import hashlib

from pipeline.catalog.hashing import hash_file


def test_hash_file_matches_stdlib(tmp_path):
    p = tmp_path / "a.bin"
    data = b"hello world" * 1000
    p.write_bytes(data)

    sha256, md5, size = hash_file(p)

    assert sha256 == hashlib.sha256(data).hexdigest()
    assert md5 == hashlib.md5(data).hexdigest()
    assert size == len(data)


def test_hash_file_identical_content_same_hash(tmp_path):
    data = b"duplicate content\n" * 50
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(data)
    b.write_bytes(data)

    ha = hash_file(a)
    hb = hash_file(b)

    assert ha == hb


def test_hash_file_different_content_different_hash(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"content A")
    b.write_bytes(b"content B")

    ha, *_ = hash_file(a)
    hb, *_ = hash_file(b)

    assert ha != hb
