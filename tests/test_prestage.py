"""Tests for the M1 input pre-stage (spec §7/§8): doc lifecycle + routing/expansion."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ingestion import doc_lifecycle, prestage

# --- doc_lifecycle dispatchability (§8) ---


def test_dispatchable_excludes_terminal():
    for s in ("done", "failed", "needs-review"):
        assert doc_lifecycle.is_dispatchable({"status": s}) is False


def test_dispatchable_excludes_in_flight():
    for s in ("expanding", "ocr", "parsed", "extracted", "deduped", "enriched"):
        assert doc_lifecycle.is_dispatchable({"status": s}) is False


def test_dispatchable_true_for_held_and_routed():
    assert doc_lifecycle.is_dispatchable({"status": "held_unprocessed"}) is True
    assert doc_lifecycle.is_dispatchable({"status": "routed"}) is True


def test_upsert_writes_doc_record():
    table = MagicMock()
    with patch.object(doc_lifecycle, "_table", return_value=table):
        doc_lifecycle.upsert(
            "d1", status="held_unprocessed", track="narrative", media_type="pdf"
        )
    item = table.put_item.call_args.kwargs["Item"]
    assert item["cache_key"] == "doc#d1"
    assert item["track"] == "narrative"
    assert item["status"] == "held_unprocessed"


def test_list_dispatchable_sorts_fifo_and_filters():
    items = [
        {"cache_key": "doc#b", "doc_id": "b", "status": "held_unprocessed"},
        {"cache_key": "doc#a", "doc_id": "a", "status": "done"},  # filtered
        {"cache_key": "doc#c", "doc_id": "c", "status": "held_unprocessed"},
    ]
    table = MagicMock()
    table.scan.return_value = {"Items": items}
    with patch.object(doc_lifecycle, "_table", return_value=table):
        out = doc_lifecycle.list_dispatchable()
    assert [r["doc_id"] for r in out] == ["b", "c"]  # 'a' done, sorted


# --- prestage routing (§7) ---


def test_route_pdf_to_narrative(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    media, track, phase = prestage.route_file(p)
    assert media == "pdf"
    assert track == "narrative"
    assert phase == "phase1"


def test_route_image_to_vision(tmp_path):
    p = tmp_path / "map.jpg"
    # minimal JPEG magic bytes
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    media, track, _ = prestage.route_file(p)
    assert media == "image"
    assert track == "vision"


def test_prestage_file_records_needs_review_for_unsupported(tmp_path):
    p = tmp_path / "weird.xyz"
    p.write_bytes(b"nonsense")
    with patch.object(prestage.doc_lifecycle, "upsert") as up:
        prestage.prestage_file(p)
    kwargs = up.call_args.kwargs
    assert kwargs["status"] == "needs-review"
    assert kwargs["track"] == "skip"


# --- archive expansion (§7, resumable + holdings dedup) ---


def test_expand_zip_extracts_files(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.pdf", "%PDF a")
        zf.writestr("b.txt", "hello")
    dest = tmp_path / "out"
    extracted = prestage.expand_archive(archive, dest)
    names = sorted(p.name for p in extracted)
    assert names == ["a.pdf", "b.txt"]
    assert (dest / "a.pdf").exists()


def test_expand_zip_skips_held_files(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("held.pdf", "x")
        zf.writestr("new.pdf", "y")
    dest = tmp_path / "out"
    extracted = prestage.expand_archive(archive, dest, held_names={"held.pdf"})
    names = [p.name for p in extracted]
    assert names == ["new.pdf"]  # held.pdf skipped


def test_prestage_dir_expands_and_routes(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    # a loose pdf + a zip containing an image
    (root / "loose.pdf").write_bytes(b"%PDF loose")
    archive = root / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pic.jpg", b"\xff\xd8\xff\xe0stuff".decode("latin-1"))
    with patch.object(prestage.doc_lifecycle, "upsert"):
        summary = prestage.prestage_dir(root, tmp_path / "expanded")
    # loose.pdf -> narrative, pic.jpg -> vision
    assert summary.get("narrative", 0) >= 1
    assert summary.get("vision", 0) >= 1
