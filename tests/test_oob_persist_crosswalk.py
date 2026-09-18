"""Tests for OOB persistence + name->PersonID crosswalk (Piece 2 / C1)."""

import json
from pathlib import Path

from src.ingestion.oob_markdown.command_staff import parse_command_staff
from src.ingestion.oob_markdown.crosswalk import (
    MATCH_EXACT,
    MATCH_FUZZY,
    MATCH_NONE,
    build_command_staff_crosswalk,
)
from src.ingestion.oob_markdown.models import CommandStaffRow
from src.ingestion.oob_markdown.persist import persist_parse_result

CMD_MD = """
82d Airborne Division

COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>15 Sep 1943</td><td>Maj Gen Matthew B Ridgway</td></tr>
<tr><td>Arty Comdr</td><td>15 Sep 1943</td><td>Brig Gen Anthony C McLuliffe</td></tr>
</tbody></table>
"""


# --- persistence ---------------------------------------------------------


def test_persist_writes_section_file(tmp_path: Path) -> None:
    result = parse_command_staff(CMD_MD, "82nd_airborne.md")
    out = persist_parse_result(result, "command_staff", tmp_path)
    assert out.exists()
    assert out.parent == tmp_path / "oob" / "command_staff"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["row_count"] == 2
    assert data["rows"][0]["name"] == "Matthew B Ridgway"
    # write_json_with_lock stamps metadata.
    assert "_schema_version" in data
    assert "_last_updated" in data


def test_persist_slug_from_source(tmp_path: Path) -> None:
    result = parse_command_staff(CMD_MD, "82nd_airborne.md")
    out = persist_parse_result(result, "command_staff", tmp_path)
    assert out.name == "82nd_airborne.json"


def test_persist_does_not_touch_people_dir(tmp_path: Path) -> None:
    people = tmp_path / "people"
    people.mkdir()
    result = parse_command_staff(CMD_MD, "x.md")
    persist_parse_result(result, "command_staff", tmp_path)
    assert list(people.iterdir()) == []  # untouched


# --- crosswalk (exact match) ---------------------------------------------


def _fake_people(tmp_path: Path) -> Path:
    """Create a minimal people dir with an index.json mapping name->filename."""
    people = tmp_path / "people"
    people.mkdir()
    # Person file with a ULID in the filename so build_name_index reads the id
    # from the filename.
    (people / "Matthew_B_Ridgway_01HZZZRIDGWAY01.json").write_text(
        json.dumps({"PersonID": "01HZZZRIDGWAY01", "name": "Matthew B Ridgway"}),
        encoding="utf-8",
    )
    index = {"matthew b ridgway": "Matthew_B_Ridgway_01HZZZRIDGWAY01.json"}
    (people / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return people


def _rows():
    return [
        CommandStaffRow(
            division="82d Airborne Division",
            position="Comdg Gen",
            effective_date="15 Sep 1943",
            rank="Maj Gen",
            name="Matthew B Ridgway",
            source_file="82nd_airborne.md",
        ),
        CommandStaffRow(
            division="82d Airborne Division",
            position="Arty Comdr",
            effective_date="15 Sep 1943",
            rank="Brig Gen",
            name="Anthony C McLuliffe",  # garbled (McAuliffe) -> must NOT match
            source_file="82nd_airborne.md",
        ),
    ]


def test_exact_match_resolves_person_id(tmp_path: Path) -> None:
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_rows(), people)
    ridgway = result.links[0]
    assert ridgway.person_id == "01HZZZRIDGWAY01"
    assert ridgway.match_method == MATCH_EXACT
    assert ridgway.needs_review is False


def test_garbled_name_does_not_false_match(tmp_path: Path) -> None:
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_rows(), people)
    garbled = result.links[1]
    # "McLuliffe" has no candidate at all in this store -> unmatched, flagged.
    assert garbled.person_id is None
    assert garbled.match_method == MATCH_NONE
    assert garbled.needs_review is True
    assert "no exact or fuzzy" in garbled.notes.lower()


def test_crosswalk_counts(tmp_path: Path) -> None:
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_rows(), people)
    assert len(result.links) == 2
    assert result.matched_count == 1
    assert result.review_count == 1


def test_crosswalk_empty_people_dir(tmp_path: Path) -> None:
    # No people store yet: everything is unmatched, nothing errors.
    result = build_command_staff_crosswalk(_rows(), tmp_path / "nonexistent_people")
    assert result.matched_count == 0
    assert all(link.match_method == MATCH_NONE for link in result.links)


def test_crosswalk_is_serializable(tmp_path: Path) -> None:
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_rows(), people)
    data = result.to_dict()
    assert data["link_count"] == 2
    assert data["matched_count"] == 1
    assert isinstance(data["links"], list)
    # Round-trips through JSON.
    json.loads(json.dumps(data))


# --- crosswalk (fuzzy match + normalization) -----------------------------


def _one_row(name: str) -> list:
    return [
        CommandStaffRow(
            division="82d Airborne Division",
            position="Comdg Gen",
            effective_date="15 Sep 1943",
            rank="Maj Gen",
            name=name,
            source_file="82nd_airborne.md",
        )
    ]


def test_fuzzy_match_flags_for_review(tmp_path: Path) -> None:
    # OCR dropped a letter: "Ridgeway" vs indexed "Ridgway" -> fuzzy, reviewed.
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_one_row("Matthew B Ridgeway"), people)
    link = result.links[0]
    assert link.person_id == "01HZZZRIDGWAY01"
    assert link.match_method == MATCH_FUZZY
    assert link.needs_review is True
    assert 0.0 < link.confidence < 1.0
    assert "fuzzy" in link.notes.lower()


def test_fuzzy_gate_blocks_different_last_name(tmp_path: Path) -> None:
    # Same store (only Ridgway). A wholly different surname must not fuzzy-match.
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_one_row("Matthew B Bradley"), people)
    link = result.links[0]
    assert link.person_id is None
    assert link.match_method == MATCH_NONE


def test_exact_match_survives_punctuation(tmp_path: Path) -> None:
    # Index keyed plain-lowercase would miss on punctuation; normalize_name
    # re-keying makes "Matthew B. Ridgway" an EXACT match to "Matthew B Ridgway".
    people = _fake_people(tmp_path)
    result = build_command_staff_crosswalk(_one_row("Matthew B. Ridgway,"), people)
    link = result.links[0]
    assert link.person_id == "01HZZZRIDGWAY01"
    assert link.match_method == MATCH_EXACT
    assert link.needs_review is False
