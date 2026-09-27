from pathlib import Path

from pipeline.catalog.run import group_by_sha
from pipeline.catalog.walk import CatalogEntry


def entry(rel_path, folder_label, zip_member=None):
    return CatalogEntry(abs_path=Path("/x") / rel_path, rel_path=rel_path,
                         folder_label=folder_label, zip_member=zip_member)


def test_group_by_sha_groups_cross_folder_duplicates():
    entries = [
        entry("Dataset/Land_documents/A/dup1.pdf", "A"),
        entry("Dataset/Land_documents/B/dup2.pdf", "B"),
        entry("Dataset/Land_documents/A/unique.pdf", "A"),
    ]
    hashes = {
        "Dataset/Land_documents/A/dup1.pdf": ("sha-dup", "md5-dup", 100, None),
        "Dataset/Land_documents/B/dup2.pdf": ("sha-dup", "md5-dup", 100, None),
        "Dataset/Land_documents/A/unique.pdf": ("sha-uniq", "md5-uniq", 50, None),
    }

    groups, errors = group_by_sha(entries, hashes)

    assert errors == []
    assert len(groups) == 2
    dup_group = groups["sha-dup"]
    assert len(dup_group["paths"]) == 2
    assert dup_group["folder_labels"] == {"A", "B"}
    # primary path is deterministic: the lexicographically-first rel_path
    assert dup_group["primary"].rel_path == "Dataset/Land_documents/A/dup1.pdf"


def test_group_by_sha_dotfile_and_normal_name_collapse_to_one_group():
    """Regression for the real ' Court Deposit/.pdf' duplicate: a byte-identical file with an
    unusual name must land in the same document group, not create a spurious extra document."""
    entries = [
        entry("Dataset/Land_documents/X/Court Deposit/.pdf", "Court Deposit"),
        entry("Dataset/Land_documents/X/Court Deposit/Covering_Letter_247_2.pdf", "Court Deposit"),
    ]
    hashes = {e.rel_path: ("sha-same", "md5-same", 12345, None) for e in entries}

    groups, _ = group_by_sha(entries, hashes)

    assert len(groups) == 1
    g = groups["sha-same"]
    assert len(g["paths"]) == 2
    # '.' (0x2E) sorts before 'C' (0x43): the dotfile path is the deterministic primary.
    assert g["primary"].rel_path.endswith("/.pdf")


def test_group_by_sha_records_hash_errors_and_skips_them():
    entries = [entry("Dataset/Land_documents/A/bad.pdf", "A")]
    hashes = {"Dataset/Land_documents/A/bad.pdf": ("", "", 0, "PermissionError: denied")}

    groups, errors = group_by_sha(entries, hashes)

    assert groups == {}
    assert errors == [("Dataset/Land_documents/A/bad.pdf", "PermissionError: denied")]


def test_group_by_sha_zip_overlap_detection():
    entries = [
        entry("Dataset/Land_documents/LA_Patta_land_Documents/Form E/e.pdf", "Form E"),
        entry("data/unzipped/F FORM UNITS/BLOCK 1/e.pdf", "Form E", zip_member="F FORM UNITS/BLOCK 1/e.pdf"),
    ]
    hashes = {e.rel_path: ("sha-both", "md5", 10, None) for e in entries}

    groups, _ = group_by_sha(entries, hashes)

    g = groups["sha-both"]
    assert g["zip_members"] == {"F FORM UNITS/BLOCK 1/e.pdf"}
    has_nonzip = any(e.zip_member is None for e in g["paths"])
    assert has_nonzip is True
