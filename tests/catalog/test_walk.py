from pathlib import Path

from pipeline.catalog.walk import discover


def _touch(p: Path, content: bytes = b"%PDF-1.4\n%%EOF") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_discover_walks_land_documents_and_folder_label(tmp_path):
    repo = tmp_path
    _touch(repo / "Dataset/Land_documents/Government Order (GO)/go.pdf")
    _touch(repo / "Dataset/Land_documents/LA_Patta_land_Documents/Form E/e1.pdf")
    _touch(repo / "Dataset/Land_documents/LA_Patta_land_Documents/Form E/sub/e2.pdf")
    # a non-pdf file must be ignored
    _touch(repo / "Dataset/Land_documents/Government Order (GO)/notes.txt")

    entries = discover(repo, repo / "data/unzipped")

    rels = {e.rel_path for e in entries}
    assert "Dataset/Land_documents/Government Order (GO)/go.pdf" in rels
    assert "Dataset/Land_documents/LA_Patta_land_Documents/Form E/e1.pdf" in rels
    assert not any(r.endswith("notes.txt") for r in rels)

    by_path = {e.rel_path: e for e in entries}
    go = by_path["Dataset/Land_documents/Government Order (GO)/go.pdf"]
    assert go.folder_label == "Government Order (GO)"
    assert go.zip_member is None

    e2 = by_path["Dataset/Land_documents/LA_Patta_land_Documents/Form E/sub/e2.pdf"]
    assert e2.folder_label == "sub"  # immediate parent, per land-domain-knowledge §3


def test_discover_matches_dotfile_named_pdf(tmp_path):
    """Regression: Path.suffix on a name like '.pdf' is '' (pathlib quirk), which would
    silently drop a real file. See Dataset/.../Court Deposit/.pdf in the real corpus."""
    repo = tmp_path
    _touch(repo / "Dataset/Land_documents/LA_Patta_land_Documents/Court Deposit/.pdf")

    entries = discover(repo, repo / "data/unzipped")

    assert any(e.rel_path.endswith("Court Deposit/.pdf") for e in entries)


def test_discover_includes_zip_members(tmp_path):
    import zipfile

    repo = tmp_path
    zip_dir = repo / "Dataset/Land_documents/LA_Patta_land_Documents/Form E"
    zip_dir.mkdir(parents=True)
    zpath = zip_dir / "F_FORM_UNITS.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("F FORM UNITS/BLOCK 1/", "")  # a directory entry
        z.writestr("F FORM UNITS/BLOCK 1/a.pdf", "%PDF-1.4\n%%EOF")

    entries = discover(repo, repo / "data/unzipped")

    zip_entries = [e for e in entries if e.zip_member is not None]
    assert len(zip_entries) == 1
    assert zip_entries[0].zip_member == "F FORM UNITS/BLOCK 1/a.pdf"
    assert zip_entries[0].folder_label == "Form E"
    assert (repo / "data/unzipped/F FORM UNITS/BLOCK 1/a.pdf").exists()


def test_discover_is_deterministically_sorted(tmp_path):
    repo = tmp_path
    _touch(repo / "Dataset/Land_documents/Form F/z.pdf")
    _touch(repo / "Dataset/Land_documents/Form F/a.pdf")

    entries = discover(repo, repo / "data/unzipped")
    rels = [e.rel_path for e in entries]
    assert rels == sorted(rels)
