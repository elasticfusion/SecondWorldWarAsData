"""Reusable Grokipedia/Wikipedia enrichment gate: stamp every check, diff the revised
entry, limit updates via staleness window."""

import time

from src.enrichment.enrichment_gate import (
    DEFAULT_RECHECK_SECONDS,
    apply_enrichment_diff,
    diff_enrichment,
    should_check_enrichment,
    stamp_checked,
)


def test_should_check_when_never_checked():
    assert should_check_enrichment({}) is True


def test_limit_updates_skips_recent_check():
    rec = {"enrichment_checked_at": int(time.time())}
    assert should_check_enrichment(rec) is False


def test_rechecks_after_window():
    rec = {"enrichment_checked_at": int(time.time()) - DEFAULT_RECHECK_SECONDS - 1}
    assert should_check_enrichment(rec) is True


def test_stamp_sets_checked_and_last_updated():
    rec = {}
    stamp_checked(rec)
    assert rec["enrichment_checked_at"] > 0
    assert rec["_last_updated"]  # ISO date


def test_diff_reports_only_real_changes():
    existing = {"category": "armor", "country_of_origin": "USA"}
    incoming = {
        "category": "armor",
        "country_of_origin": "USA",
        "specifications": {"w": 1},
    }
    assert diff_enrichment(existing, incoming) == ["specifications"]  # only the new key
    # empties are ignored
    assert diff_enrichment(existing, {"description": ""}) == []


def test_apply_diff_writes_changes_and_stamps():
    existing = {"category": "armor"}
    changed = apply_enrichment_diff(
        existing, {"category": "armor", "country_of_origin": "USA"}
    )
    assert changed == ["country_of_origin"]
    assert existing["country_of_origin"] == "USA"
    assert existing["enrichment_checked_at"] > 0
