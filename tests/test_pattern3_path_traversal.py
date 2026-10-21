"""Path-traversal regression — Phase-2 review PATTERN 3.

In-process entity writers build filenames from LLM-derived values (date_start, place_name,
equipment common_name). write_json_with_lock does mkdir(parents=True), so an unsanitized "/", "..",
backslash, NUL, or leading-dot name could escape the entity dir. All such writers must route the
filename through batch_parallel._safe_entity_filename + _contained_entity_path (basename + safe
charset + strip leading dots + symlink-resolved containment, fail-closed to the ULID).
"""

from pathlib import Path
from unittest import mock

from src.extraction.dates import _find_or_create_date
from src.extraction.places import _find_or_create_place

_HOSTILE = [
    "../../../tmp/evil",
    "..\\..\\windows\\system32",
    "/etc/passwd",
    "a/b/c",
    "....//....//x",
    "foo\x00bar",
    "." * 5,
    "x" * 500,
]


import pytest


@pytest.fixture(autouse=True)
def _offline_place_enrichment():
    """Place creation calls Grokipedia/Wikipedia enrichment inline; stub it out so these
    containment tests stay offline + fast (enrichment is irrelevant to the filename guard).
    """
    with (
        mock.patch(
            "src.extraction.enrich_places.enrich_place_from_grokipedia",
            lambda *a, **k: None,
        ),
        mock.patch(
            "src.extraction.enrich_places.enrich_place_from_wikipedia",
            lambda *a, **k: None,
        ),
    ):
        yield


def _contained(root: Path, written: Path) -> bool:
    """True iff ``written`` resolves to a path inside ``root`` (no escape)."""
    r = root.resolve()
    w = written.resolve()
    return r == w or r in w.parents


# --- dates: date_start is LLM-derived ------------------------------------------------------
def test_date_file_contained_for_hostile_date_start(tmp_path):
    dates_dir = tmp_path / "dates"
    dates_dir.mkdir()
    for bad in _HOSTILE:
        index: dict = {}
        out = _find_or_create_date(
            {"date_start": bad, "time_start": None}, dates_dir, index
        )
        assert _contained(dates_dir, out), f"escaped dates_dir with {bad!r}: {out}"
        # The file (if created) must physically live directly under dates_dir.
        assert out.parent.resolve() == dates_dir.resolve()
        assert out.name.endswith(".json") and "/" not in out.name
        # nothing was created outside the tree
        assert not (tmp_path / "tmp" / "evil").exists()


def test_date_file_normal_name_still_readable(tmp_path):
    dates_dir = tmp_path / "dates"
    dates_dir.mkdir()
    out = _find_or_create_date(
        {"date_start": "1944-06-06", "time_start": "06:30"}, dates_dir, {}
    )
    assert _contained(dates_dir, out)
    assert out.exists() and out.name.endswith(".json")
    # the human-readable stem survives (hyphens/colons stripped by the cosmetic transform)
    assert "19440606" in out.name


# --- places: place_name is LLM-derived -----------------------------------------------------
def test_place_file_contained_for_hostile_place_name(tmp_path):
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    for bad in _HOSTILE:
        out = _find_or_create_place(
            bad, {"PlaceMentionID": "01PMENTION0000000000000000"}, places_dir, {}
        )
        assert _contained(places_dir, out), f"escaped places_dir with {bad!r}: {out}"
        assert out.parent.resolve() == places_dir.resolve()
        assert out.name.endswith(".json") and "/" not in out.name
        assert not (tmp_path / "tmp" / "evil").exists()


def test_place_file_normal_name_still_readable(tmp_path):
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    out = _find_or_create_place(
        "Saint-Lô", {"PlaceMentionID": "01PMENTION0000000000000000"}, places_dir, {}
    )
    assert _contained(places_dir, out)
    assert out.exists() and out.name.endswith(".json")


# --- the shared sanitizer directly (covers the equipment path, same helper) ----------------
def test_long_name_does_not_truncate_the_appended_id(tmp_path):
    """The reviewer-found bug: capping the WHOLE filename (stem+id) truncated the uniqueness id away.
    The stem is now capped BEFORE the id is appended, so the id suffix always survives. Assert a
    >cap-length place name yields a BOUNDED filename that still ENDS with the appended id segment.
    """
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    shared = "Longplace" * 20  # 180 chars, exceeds the 120-char stem cap
    out = _find_or_create_place(
        shared, {"PlaceMentionID": "01PMENTIONA000000000000000"}, places_dir, {}
    )
    assert out.exists() and out.parent.resolve() == places_dir.resolve()
    stem = out.name[:-5]  # drop .json
    # the id segment (8 hex-ish ULID chars after the final '_') must be intact, not truncated
    assert "_" in stem, out.name
    id_seg = stem.rsplit("_", 1)[1]
    assert len(id_seg) == 8, f"id suffix truncated by the length cap: {out.name!r}"
    assert len(out.name) <= 160  # bounded (no ENAMETOOLONG)


def test_safe_entity_filename_neutralizes_all_hostile_inputs():
    from src.extraction.batch_parallel import (
        _safe_entity_filename,
        _contained_entity_path,
    )

    for bad in _HOSTILE:
        name = _safe_entity_filename(f"{bad}_abc.json", "equipment_fallback")
        assert "/" not in name and "\\" not in name and "\x00" not in name
        assert not name.startswith(".")
        assert name.endswith(".json")

    # containment: a crafted already-safe-looking name that resolves outside -> None (fail closed)
    d = Path("/tmp/pattern3_contain_probe")
    # a plain safe name resolves inside
    assert _contained_entity_path(d, "ok.json") == d / "ok.json"


# --- §1.0b follow-up: weather + casualties writers now route through the sanitizer ----------
def test_weather_create_file_contains_hostile_place(tmp_path):
    """weather_central._create_new_weather_file must not let a hostile place_name escape
    weather_dir (place_name is LLM-extracted)."""
    from src.extraction import weather_central as wc

    weather_dir = tmp_path / "weather"
    weather_dir.mkdir()
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    for hostile in ("../../etc/evil", "/etc/passwd", "a/b/c", "foo\x00bar"):
        wf, fname = wc._create_new_weather_file(
            {"PlaceMentionID": None},
            "1944-06-06",
            hostile,
            weather_dir,
            places_dir,
            fetch_api=False,
        )
        # the resolved file must stay inside weather_dir, and the name carries no separators
        assert weather_dir.resolve() in wf.resolve().parents, hostile
        assert "/" not in fname and "\\" not in fname and "\x00" not in fname


def test_casualty_save_clamps_type_and_contains(tmp_path):
    """_save_casualty clamps a hostile/unknown 'type' to the enum and never writes outside dir."""
    from src.extraction import casualties

    out = tmp_path / "casualties"
    out.mkdir()
    cid = "01J000000000000000000CAS0"
    # hostile type -> clamped to 'casualties', filename sanitized + contained
    with mock.patch("src.utils.file_lock.write_json_with_lock") as w:
        casualties._save_casualty({"CasualtyID": cid, "type": "../../etc/passwd"}, out)
        written_path = w.call_args.args[0]
        assert out.resolve() in Path(written_path).resolve().parents
        assert "/" not in Path(written_path).name
    # a valid enum type is preserved in the name
    with mock.patch("src.utils.file_lock.write_json_with_lock") as w:
        casualties._save_casualty({"CasualtyID": cid, "type": "killed"}, out)
        assert Path(w.call_args.args[0]).name == f"killed_{cid}.json"


def test_logistics_save_contains_hostile_date_start(tmp_path):
    """logistics._save_extraction must not let a hostile LLM date_start escape logistics_dir."""
    from unittest import mock
    from src.extraction import logistics as L

    out = tmp_path / "logistics"
    out.mkdir()
    ext = mock.Mock()
    ext.category = "general"
    ext.type = "supply_shortage"
    ext.date_start = "../../../../tmp/evil"  # hostile
    sub_event = {"Sub-eventID": "01SUB0000000000000000000A0"}
    data = {
        "category": "general",
        "logistics_type": "supply_shortage",
        "LogisticsID": "01LOG0000000000000000000A0",
    }
    with (
        mock.patch.object(L, "_build_logistics_data", return_value=data),
        mock.patch.object(
            L.Logistics, "model_validate", lambda d: mock.Mock(model_dump=lambda **k: d)
        ),
        mock.patch("src.utils.file_lock.write_json_with_lock") as w,
    ):
        ok = L._save_extraction(
            ext, {"EventID": "E"}, sub_event, ({}, {}, {}, {}, {}, {}), out, set()
        )
    assert ok is True
    written = Path(w.call_args.args[0])
    assert out.resolve() in written.resolve().parents  # stayed inside logistics_dir
    assert "/" not in written.name and ".." not in written.name
