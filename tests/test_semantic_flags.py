"""Tests for semantic quality flags (HIGH-2) — flag, never block/fabricate."""

from src.utils import semantic_flags as sf


# --- geocode plausibility (places) — GLOBAL (WWII is a global conflict; no theater box) ---
def test_geo_valid_coords_no_flag_anywhere_on_earth():
    # A legitimate European place...
    assert sf.flags_for("places", {"latitude": 49.5, "longitude": 2.5}) == []
    # ...AND a legitimate NON-European mention (Pacific/Washington/Moscow) must NOT be flagged.
    assert sf.flags_for("places", {"latitude": 38.9, "longitude": -77.0}) == []  # DC
    assert sf.flags_for("places", {"latitude": 35.6, "longitude": 139.7}) == []  # Tokyo


def test_geo_impossible_coords_flagged():
    # Physically impossible (garbage geocode) — lon beyond ±180 / lat beyond ±90.
    assert sf.flags_for("places", {"latitude": 10.0, "longitude": -210.0}) == [
        "geo_impossible_coords"
    ]
    assert sf.flags_for("places", {"latitude": 95.0, "longitude": 2.0}) == [
        "geo_impossible_coords"
    ]


def test_geo_missing_or_zero_coords_flagged():
    assert sf.flags_for("places", {"latitude": None, "longitude": None}) == [
        "geo_missing_coords"
    ]
    assert sf.flags_for("places", {"latitude": 0.0, "longitude": 0.0}) == [
        "geo_missing_coords"
    ]


# --- date-in-window (dates) ---
def test_date_in_window_no_flag():
    assert sf.flags_for("dates", {"resolved_earliest": "1944-12-16"}) == []


def test_date_out_of_window_flagged():
    assert sf.flags_for("dates", {"resolved_earliest": "1946-03-01"}) == [
        "date_out_of_window"
    ]


def test_approximate_date_not_flagged():
    # Non-ISO approximate form is intentionally NOT flagged (it legitimately resolves to a range).
    assert sf.flags_for("dates", {"date_start": "summer-1944"}) == []


# --- citation resolvability (bibliography) ---
def test_citation_stub_flagged():
    assert sf.flags_for("bibliography", {"title": "ibid."}) == ["citation_stub"]
    assert sf.flags_for("bibliography", {"title": "26"}) == ["citation_stub"]


def test_citation_unresolvable_when_no_archive_no_url():
    assert sf.flags_for(
        "bibliography", {"title": "A Real Book Title", "resource_urls": []}
    ) == ["citation_unresolvable"]


def test_citation_resolvable_with_archive_or_url():
    assert (
        sf.flags_for(
            "bibliography",
            {"title": "A Real Book", "archive_reference_number": "RG 407"},
        )
        == []
    )
    assert (
        sf.flags_for(
            "bibliography", {"title": "A Real Book", "resource_urls": ["https://x"]}
        )
        == []
    )


# --- annotate: additive, idempotent, fail-safe, non-mutating of content ---
def test_annotate_is_additive_and_idempotent():
    rec = {"latitude": 0.0, "longitude": 0.0, "current_name": "X"}
    sf.annotate("places", rec)
    assert rec["quality_flags"] == ["geo_missing_coords"]
    assert rec["current_name"] == "X"  # content untouched
    sf.annotate("places", rec)  # re-run
    assert rec["quality_flags"] == ["geo_missing_coords"]  # no duplication


def test_annotate_unions_with_existing_flags():
    rec = {"latitude": 10.0, "longitude": -210.0, "quality_flags": ["prior_flag"]}
    sf.annotate("places", rec)
    assert rec["quality_flags"] == ["prior_flag", "geo_impossible_coords"]


def test_annotate_no_flags_leaves_record_unchanged():
    rec = {"latitude": 49.0, "longitude": 2.0}
    sf.annotate("places", rec)
    assert "quality_flags" not in rec  # clean record gets no field


def test_unknown_entity_and_bad_input_are_fail_safe():
    assert sf.flags_for("people", {"x": 1}) == []  # no validator -> no flags
    assert sf.flags_for("places", None) == []  # malformed -> no raise
    assert sf.annotate("places", {"latitude": "not-a-number"}) is not None


def test_stale_flag_cleared_when_problem_fixed():
    """A flag this module owns must CLEAR once the underlying problem is fixed (truthful signal)."""
    rec = {"latitude": 0.0, "longitude": 0.0}
    sf.annotate("places", rec)
    assert rec["quality_flags"] == ["geo_missing_coords"]
    # Later geocoded to valid in-theatre coords -> the stale flag is removed.
    rec["latitude"], rec["longitude"] = 49.0, 2.0
    sf.annotate("places", rec)
    assert "quality_flags" not in rec


def test_foreign_flags_preserved_when_recomputing():
    """Flags this module does NOT own are preserved across annotate (replace only our vocabulary)."""
    rec = {
        "latitude": 49.0,
        "longitude": 2.0,
        "quality_flags": ["some_other_subsystem_flag"],
    }
    sf.annotate("places", rec)
    assert rec["quality_flags"] == ["some_other_subsystem_flag"]
