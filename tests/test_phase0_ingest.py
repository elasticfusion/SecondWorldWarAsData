"""Tests for Phase 0 ingestion wiring (phase0_ingest + oob_markdown.run)."""

import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from src.ingestion.oob_markdown.run import run_oob_markdown_file

_HAVE_PANDOC = shutil.which("pandoc") is not None
_needs_pandoc = pytest.mark.skipif(not _HAVE_PANDOC, reason="pandoc not installed")

# A minimal OOB markdown file with a division title + two sections.
OOB_MD = """
82d Airborne Division

COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>15 Sep 1943</td><td>Maj Gen Matthew B Ridgway</td></tr>
</tbody></table>

ORGANIC UNITS

<table border="0">
<tr><td>504th Prcht Inf Regt</td><td>325th Gli Inf Regt</td></tr>
</table>
"""


def _make_source(tmp_path: Path) -> Path:
    ocr = tmp_path / "contentrepository" / "OOB" / "ocr_output"
    ocr.mkdir(parents=True)
    md = ocr / "82nd_airborne.md"
    md.write_text(OOB_MD, encoding="utf-8")
    return md


def test_run_oob_markdown_file_persists_and_crosswalks(tmp_path: Path) -> None:
    md = _make_source(tmp_path)
    output_root = tmp_path / "output"
    people_dir = output_root / "people"  # does not exist -> crosswalk unmatched

    summary = run_oob_markdown_file(md, output_root, people_dir)

    # Both sections parsed and persisted.
    assert "command_staff" in summary["sections"]
    assert "organic_units" in summary["sections"]
    assert (output_root / "oob" / "command_staff" / "82nd_airborne.json").exists()
    assert (output_root / "oob" / "organic_units" / "82nd_airborne.json").exists()

    # Persisted file has the expected shape + metadata stamp.
    data = json.loads(
        (output_root / "oob" / "command_staff" / "82nd_airborne.json").read_text()
    )
    assert data["rows"][0]["name"] == "Matthew B Ridgway"
    assert data["rows"][0]["division"] == "82d Airborne Division"
    assert "_schema_version" in data

    # Crosswalk written; no people store -> unmatched but recorded.
    assert "crosswalk" in summary
    assert (output_root / "oob" / "crosswalk" / "82nd_airborne.json").exists()
    assert summary["crosswalk"]["matched"] == 0


def test_run_oob_markdown_file_matches_existing_person(tmp_path: Path) -> None:
    md = _make_source(tmp_path)
    output_root = tmp_path / "output"
    people = output_root / "people"
    people.mkdir(parents=True)
    (people / "index.json").write_text(
        json.dumps({"matthew b ridgway": "Matthew_B_Ridgway_01HZZZRIDGWAY1.json"}),
        encoding="utf-8",
    )
    (people / "Matthew_B_Ridgway_01HZZZRIDGWAY1.json").write_text(
        json.dumps({"PersonID": "01HZZZRIDGWAY1", "name": "Matthew B Ridgway"}),
        encoding="utf-8",
    )

    summary = run_oob_markdown_file(md, output_root, people)
    assert summary["crosswalk"]["matched"] == 1


def test_discover_oob_markdown_finds_ocr_output(tmp_path: Path) -> None:
    from phase0_ingest import discover_oob_markdown

    ocr = tmp_path / "contentrepository" / "OOB" / "ocr_output"
    ocr.mkdir(parents=True)
    (ocr / "26th_infantry.md").write_text("x", encoding="utf-8")
    (ocr / "00-missing.md").write_text("skip me", encoding="utf-8")
    # A markdown file NOT under ocr_output should be ignored.
    other = tmp_path / "contentrepository" / "Book" / "chapter1"
    other.mkdir(parents=True)
    (other / "chapter1-content.md").write_text("prose", encoding="utf-8")

    found = discover_oob_markdown(tmp_path / "contentrepository")
    names = {p.name for p in found}
    assert "26th_infantry.md" in names
    assert "00-missing.md" not in names  # skip stem
    assert "chapter1-content.md" not in names  # not under ocr_output


def test_discover_oob_markdown_skips_misfiled_and_reference(tmp_path: Path) -> None:
    from phase0_ingest import discover_oob_markdown

    ocr = tmp_path / "contentrepository" / "OOB" / "ocr_output"
    ocr.mkdir(parents=True)
    # A real per-division file is kept.
    (ocr / "9th_infantry.md").write_text("x", encoding="utf-8")
    # The confirmed misfiled duplicate (holds 14th Armored, not 1st Infantry) is
    # skipped by name; its data is sourced from the Chandra 14th_armored file.
    (ocr / "1st_infantry.md").write_text("x", encoding="utf-8")
    # Aggregate/reference files (no division-type stem) are skipped.
    (ocr / "preface.md").write_text("x", encoding="utf-8")
    (ocr / "tables_of_organic_units.md").write_text("x", encoding="utf-8")

    names = {p.name for p in discover_oob_markdown(tmp_path / "contentrepository")}
    assert "9th_infantry.md" in names
    assert "1st_infantry.md" not in names  # misfiled duplicate, skipped
    assert "preface.md" not in names  # reference, not per-division
    assert "tables_of_organic_units.md" not in names


def test_discover_oob_markdown_finds_book_named_single_division(tmp_path: Path) -> None:
    from phase0_ingest import discover_oob_markdown

    # A Chandra file misnamed after the book but holding one real division is
    # included; an empty stub with the same name is not.
    real = tmp_path / "contentrepository" / "OOB_chandra" / "ETO_Order_of_Battle"
    real.mkdir(parents=True)
    (real / "ETO_Order_of_Battle.md").write_text(
        "1st Infantry Division\n\nCOMMAND AND STAFF\n", encoding="utf-8"
    )
    stub = tmp_path / "contentrepository" / "OOB_chandra.md" / "ETO_Order_of_Battle"
    stub.mkdir(parents=True)
    (stub / "ETO_Order_of_Battle.md").write_text("\n", encoding="utf-8")

    found = discover_oob_markdown(tmp_path / "contentrepository")
    real_included = any(p.parent.parent.name == "OOB_chandra" for p in found)
    stub_included = any(p.parent.parent.name == "OOB_chandra.md" for p in found)
    assert real_included  # single-division book-named file is kept
    assert not stub_included  # empty stub is skipped


def test_discover_pdfs_excludes_ocr_output(tmp_path: Path) -> None:
    from phase0_ingest import discover_pdfs

    root = tmp_path / "contentrepository"
    (root / "OOB").mkdir(parents=True)
    (root / "OOB" / "source.pdf").write_bytes(b"%PDF-1.4 fake")
    # A PDF already inside ocr_output is an output, not a source -> excluded.
    (root / "OOB" / "ocr_output").mkdir(parents=True)
    (root / "OOB" / "ocr_output" / "derived.pdf").write_bytes(b"%PDF-1.4 fake")

    found = discover_pdfs(root)
    names = {p.name for p in found}
    assert "source.pdf" in names
    assert "derived.pdf" not in names


def test_ecs_download_inputs_routes_phase0(monkeypatch) -> None:
    # The ecs entrypoint must route a phase0 script to the phase0 downloader.
    # Verify the dispatch branch exists without invoking S3. ecs_entrypoint reads
    # AWS env at import, so provide dummies (we only inspect source here).
    import inspect

    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    import ecs_entrypoint

    src = inspect.getsource(ecs_entrypoint._download_inputs)
    assert "phase0" in src
    assert hasattr(ecs_entrypoint, "_download_phase0_inputs")
    # _final_sync must handle phase0 (so output/oob reaches S3).
    final_src = inspect.getsource(ecs_entrypoint._final_sync)
    assert "phase0" in final_src and "output/oob" in final_src


# --- document (docx/epub/txt) -> markdown conversion pre-step ----------------


def test_convert_documents_enabled_gate(monkeypatch) -> None:
    from phase0_ingest import _convert_documents_enabled

    # Off by default.
    assert _convert_documents_enabled({}) is False
    assert (
        _convert_documents_enabled({"ingestion": {"convert_documents": False}}) is False
    )
    # Config opt-in.
    assert (
        _convert_documents_enabled({"ingestion": {"convert_documents": True}}) is True
    )
    # Env opt-in.
    monkeypatch.setenv("PHASE0_CONVERT_DOCUMENTS", "1")
    assert _convert_documents_enabled({}) is True


def test_discover_text_documents_excludes_ocr_output(tmp_path: Path) -> None:
    from phase0_ingest import discover_text_documents

    root = tmp_path / "contentrepository"
    (root / "Book").mkdir(parents=True)
    (root / "Book" / "notes.txt").write_text("plain", encoding="utf-8")
    (root / "Book" / "report.docx").write_bytes(b"PK\x03\x04fake")
    (root / "Book" / "vol.epub").write_bytes(b"PK\x03\x04fake")
    # A converted output already in ocr_output is not a source -> excluded.
    (root / "Book" / "ocr_output").mkdir()
    (root / "Book" / "ocr_output" / "derived.txt").write_text("x", encoding="utf-8")

    names = {p.name for p in discover_text_documents(root)}
    assert names == {"notes.txt", "report.docx", "vol.epub"}
    assert "derived.txt" not in names


def test_convert_text_documents_txt_to_markdown(tmp_path: Path) -> None:
    from phase0_ingest import _convert_text_documents

    root = tmp_path / "contentrepository"
    (root / "Book").mkdir(parents=True)
    (root / "Book" / "note.txt").write_text(
        "Section One\r\n\r\nThe 7th Armored moved out.\n", encoding="utf-8"
    )

    converted, skipped = _convert_text_documents(root, logging.getLogger("t"))
    assert converted == 1 and skipped == 0
    out = root / "Book" / "ocr_output" / "note.md"
    assert out.exists()
    md = out.read_text(encoding="utf-8")
    assert "7th Armored" in md
    assert "\r" not in md  # normalized
    # Output lands where OOB/markdown discovery would look (ocr_output sibling).
    assert "ocr_output" in out.parts


@_needs_pandoc
def test_convert_text_documents_docx_via_pandoc(tmp_path: Path) -> None:
    from phase0_ingest import _convert_text_documents

    root = tmp_path / "contentrepository"
    (root / "Book").mkdir(parents=True)
    src_md = root / "Book" / "src.md"
    src_md.write_text("# Title\n\nText about **St. Vith**.\n", encoding="utf-8")
    subprocess.run(
        ["pandoc", str(src_md), "-o", str(root / "Book" / "report.docx")],
        check=True,
        capture_output=True,
    )
    src_md.unlink()

    converted, skipped = _convert_text_documents(root, logging.getLogger("t"))
    assert converted == 1 and skipped == 0
    out = (root / "Book" / "ocr_output" / "report.md").read_text(encoding="utf-8")
    assert "St. Vith" in out.replace("\xa0", " ")


def test_convert_text_documents_empty_when_none(tmp_path: Path) -> None:
    from phase0_ingest import _convert_text_documents

    root = tmp_path / "contentrepository"
    root.mkdir(parents=True)
    converted, skipped = _convert_text_documents(root, logging.getLogger("t"))
    assert converted == 0 and skipped == 0
