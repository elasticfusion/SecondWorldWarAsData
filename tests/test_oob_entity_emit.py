"""Tests for OOB -> people entity convergence (the Huebner goal)."""

import json
from pathlib import Path

from src.extraction.people import Person
from src.ingestion.oob_markdown.crosswalk import (
    MATCH_EXACT,
    MATCH_FUZZY,
    MATCH_NONE,
    CrosswalkLink,
    CrosswalkResult,
)
from src.ingestion.oob_markdown.entity_emit import (
    PeopleEmitter,
    emit_crosswalk_to_people,
)


def _people_dir(tmp_path: Path) -> Path:
    people = tmp_path / "people"
    people.mkdir()
    # An existing narrative-sourced person with a ULID-suffixed filename.
    fname = "Clarence_R_Huebner_01HZZHUEBNER1.json"
    (people / fname).write_text(
        json.dumps(
            {
                "PersonID": "01HZZHUEBNER1AAAAAAAAAAAAA",
                "name": "Clarence R Huebner",
                "event_mentions": [],
                "biographical_profile": {"nationality": "USA", "ranks": []},
            }
        ),
        encoding="utf-8",
    )
    (people / "index.json").write_text(
        json.dumps({"clarence r huebner": fname}), encoding="utf-8"
    )
    return people


def _link(
    name: str,
    method: str,
    person_id=None,
    matched_name=None,
    confidence: float = 0.0,
) -> CrosswalkLink:
    return CrosswalkLink(
        name=name,
        rank="Maj Gen",
        position="Comdg Gen",
        division="1st Infantry Division",
        source_file="1st_infantry.md",
        person_id=person_id,
        matched_name=matched_name,
        match_method=method,
        confidence=confidence,
        needs_review=(method != MATCH_EXACT),
    )


def _result(*links: CrosswalkLink) -> CrosswalkResult:
    return CrosswalkResult(links=list(links))


def _people_files(people: Path) -> list:
    return sorted(f.name for f in people.glob("*.json") if f.name != "index.json")


# --- exact -> merge into existing -----------------------------------------


def test_exact_merges_into_existing_no_duplicate(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    before = _people_files(people)
    link = _link(
        "Clarence R Huebner",
        MATCH_EXACT,
        person_id="01HZZHUEBNER1AAAAAAAAAAAAA",
        matched_name="Clarence R Huebner",
        confidence=0.9,
    )
    summary = emit_crosswalk_to_people(_result(link), people)

    assert summary.merged == 1
    assert summary.created == 0
    # No new person file created.
    assert _people_files(people) == before

    data = json.loads((people / before[0]).read_text(encoding="utf-8"))
    # OOB rank merged into the existing person (normalized by _merge_person:
    # "Maj Gen" -> "Major General", same treatment as narrative data).
    ranks = data["biographical_profile"]["ranks"]
    assert any(r["rank"] == "Major General" for r in ranks)
    # OOB units + provenance recorded.
    units = data["biographical_profile"]["units_served"]
    assert any(u["unit"] == "1st Infantry Division" for u in units)
    sources = data["biographical_profile"]["biography_sources"]
    assert any(s["source"].startswith("OOB: 1st_infantry.md") for s in sources)
    # Existing PersonID is preserved (merge, not replace).
    assert data["PersonID"] == "01HZZHUEBNER1AAAAAAAAAAAAA"


# --- fuzzy -> mint new, flagged, no silent merge --------------------------


def test_fuzzy_mints_new_person_for_review(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    link = _link(
        "Clarence Huebner",  # fuzzy candidate for the existing person
        MATCH_FUZZY,
        person_id="01HZZHUEBNER1AAAAAAAAAAAAA",
        matched_name="Clarence R Huebner",
        confidence=0.92,
    )
    summary = emit_crosswalk_to_people(_result(link), people)

    assert summary.created == 1
    assert summary.merged == 0
    # A NEW file exists (not merged into the existing one).
    files = _people_files(people)
    assert len(files) == 2
    new_file = next(f for f in files if "01HZZHUEBNER1" not in f)
    data = json.loads((people / new_file).read_text(encoding="utf-8"))
    # New minted PersonID != the fuzzy-candidate's id (dedup will unify later).
    assert data["PersonID"] != "01HZZHUEBNER1AAAAAAAAAAAAA"
    assert "fuzzy candidate" in data["oob_convergence_note"].lower()


# --- none -> mint new ------------------------------------------------------


def test_none_mints_new_person(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    link = _link("Norman Cota", MATCH_NONE)
    summary = emit_crosswalk_to_people(_result(link), people)

    assert summary.created == 1
    files = _people_files(people)
    assert len(files) == 2
    # Index updated with the new person.
    index = json.loads((people / "index.json").read_text(encoding="utf-8"))
    assert "norman cota" in index


def test_blank_name_is_skipped(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    link = _link("", MATCH_NONE)
    summary = emit_crosswalk_to_people(_result(link), people)
    assert summary.skipped == 1
    assert summary.created == 0


# --- schema validity + idempotence ----------------------------------------


def test_emitted_people_are_valid_person_schema(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    emit_crosswalk_to_people(_result(_link("Norman Cota", MATCH_NONE)), people)
    for f in _people_files(people):
        data = json.loads((people / f).read_text(encoding="utf-8"))
        # Round-trips through the pydantic Person model (ignores extra keys).
        Person(**{k: v for k, v in data.items() if k in Person.model_fields})


def test_exact_merge_is_idempotent(tmp_path: Path) -> None:
    people = _people_dir(tmp_path)
    link = _link(
        "Clarence R Huebner",
        MATCH_EXACT,
        person_id="01HZZHUEBNER1AAAAAAAAAAAAA",
        matched_name="Clarence R Huebner",
        confidence=0.9,
    )
    emitter = PeopleEmitter(people)
    emitter.emit(_result(link))
    PeopleEmitter(people).emit(_result(link))  # second pass, same link

    data = json.loads(
        (people / "Clarence_R_Huebner_01HZZHUEBNER1.json").read_text(encoding="utf-8")
    )
    # Rank + unit + source not duplicated on re-run.
    ranks = data["biographical_profile"]["ranks"]
    assert sum(1 for r in ranks if r["rank"] == "Major General") == 1
    units = data["biographical_profile"]["units_served"]
    assert sum(1 for u in units if u["unit"] == "1st Infantry Division") == 1
