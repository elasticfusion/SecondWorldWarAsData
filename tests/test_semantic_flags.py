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


# --- date-in-window (dates): generous WWII window incl. antecedents + aftermath ---
def test_date_in_window_no_flag():
    assert sf.flags_for("dates", {"resolved_earliest": "1944-12-16"}) == []


def test_wwii_antecedents_not_flagged():
    # Pre-1939 antecedents the corpus legitimately covers must NOT be flagged.
    assert sf.flags_for("dates", {"resolved_earliest": "1935-10-03"}) == []  # Abyssinia
    assert (
        sf.flags_for("dates", {"resolved_earliest": "1936-07-17"}) == []
    )  # Spanish CW
    assert sf.flags_for("dates", {"resolved_earliest": "1938-03-12"}) == []  # Anschluss


def test_date_out_of_window_flagged():
    # Far outside any plausible WWII window (default ends 1950).
    assert sf.flags_for("dates", {"resolved_earliest": "1960-03-01"}) == [
        "date_out_of_window"
    ]


def test_approximate_date_not_flagged():
    # Non-ISO approximate form is intentionally NOT flagged (it legitimately resolves to a range).
    assert sf.flags_for("dates", {"date_start": "summer-1944"}) == []


def test_date_window_is_config_env_overridable(monkeypatch):
    """The window is config/env-driven, not a hardcoded single-theater assumption."""
    import importlib

    monkeypatch.setenv("DATE_WINDOW_START", "1944-01-01")
    monkeypatch.setenv("DATE_WINDOW_END", "1944-12-31")
    mod = importlib.reload(sf)
    try:
        # 1943 now outside the (narrowed) window -> flagged.
        assert mod.flags_for("dates", {"resolved_earliest": "1943-05-01"}) == [
            "date_out_of_window"
        ]
    finally:
        monkeypatch.delenv("DATE_WINDOW_START", raising=False)
        monkeypatch.delenv("DATE_WINDOW_END", raising=False)
        importlib.reload(sf)  # restore module-level defaults for other tests


# --- citation resolvability (bibliography) ---
def test_citation_stub_flagged():
    assert sf.flags_for("bibliography", {"title": "ibid."}) == ["citation_stub"]
    assert sf.flags_for("bibliography", {"title": "26"}) == ["citation_stub"]


def test_citation_unresolvable_when_no_archive_no_url():
    # Only flagged AFTER resolution was attempted (search_status set by the Phase-3 resolver).
    assert sf.flags_for(
        "bibliography",
        {
            "title": "A Real Book Title",
            "resource_urls": [],
            "search_status": "not_found",
        },
    ) == ["citation_unresolvable"]


def test_citation_not_flagged_before_resolution():
    # A freshly-EXTRACTED record (no search_status, written in Phase 2 before the resolver
    # runs) is NOT-YET-RESOLVED, not unresolvable — must NOT be flagged (phase-ordering fix:
    # previously tripped ~50% of bibliography writes with a false positive).
    assert (
        sf.flags_for(
            "bibliography", {"title": "A Real Book Title", "resource_urls": []}
        )
        == []
    )


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


def test_identified_published_work_not_unresolvable():
    """A biblio-note-identified published work (canonical title) with no online copy is NOT
    citation_unresolvable — it's a known book, merely not located online."""
    rec = {
        "title": "Unknown",
        "search_status": "reference_identified",
        "resource_urls": [],
        "source_metadata": {
            "biblio_category": "published_work",
            "canonical_title": "Crusade in Europe",
        },
    }
    assert sf.flags_for("bibliography", rec) == []


def test_reference_identified_status_not_unresolvable():
    rec = {
        "title": "Unknown",
        "archive_reference_number": "RG 407",
        "search_status": "reference_identified",
    }
    assert sf.flags_for("bibliography", rec) == []


def test_genuinely_unidentified_still_unresolvable():
    rec = {
        "title": "A real prose fragment",
        "search_status": "not_found",
        "resource_urls": [],
    }
    assert sf.flags_for("bibliography", rec) == ["citation_unresolvable"]


def test_citation_flags_no_crash_on_empty_title_with_citation_dict():
    """Regression: title fallback must read citation['title'], not the citation dict (which
    would crash on .strip())."""
    rec = {"title": "", "citation": {"title": "Ibid."}, "search_status": "not_found"}
    # 'Ibid.' -> stub; the key point is it does not raise.
    assert sf.flags_for("bibliography", rec) == ["citation_stub"]
