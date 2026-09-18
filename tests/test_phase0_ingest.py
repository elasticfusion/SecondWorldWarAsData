"""Tests for Phase 0 ingestion wiring (phase0_ingest + oob_markdown.run)."""

import json
from pathlib import Path

from src.ingestion.oob_markdown.run import run_oob_markdown_file

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
    (ocr / "1st_infantry.md").write_text("x", encoding="utf-8")
    (ocr / "00-missing.md").write_text("skip me", encoding="utf-8")
    # A markdown file NOT under ocr_output should be ignored.
    other = tmp_path / "contentrepository" / "Book" / "chapter1"
    other.mkdir(parents=True)
    (other / "chapter1-content.md").write_text("prose", encoding="utf-8")

    found = discover_oob_markdown(tmp_path / "contentrepository")
    names = {p.name for p in found}
    assert "1st_infantry.md" in names
    assert "00-missing.md" not in names  # skip stem
    assert "chapter1-content.md" not in names  # not under ocr_output


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
