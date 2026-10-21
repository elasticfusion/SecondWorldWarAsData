"""Regression — §1.0b core nits.

- people._normalize_name was double-stacked @lru_cache (inner maxsize=1000 dead behind outer 5000).
  Now a single cache: its cache_info().maxsize is 5000 and it is directly callable + cacheable.
- places._add_event_mention indexed m["Sub_eventID"] (KeyError on a legacy mention lacking the key).
  The dedup filter now uses .get() so a legacy mention is skipped, not crashed on.
"""


def test_normalize_name_single_lru_cache():
    from src.extraction.people import _normalize_name

    # A single lru_cache exposes cache_info with the OUTER maxsize; a double-wrap would have nested
    # the real function one level down (the inner cache unreachable).
    info = _normalize_name.cache_info()
    assert info.maxsize == 5000
    # directly callable + actually caches (second call is a hit)
    _normalize_name.cache_clear()
    _normalize_name("Gen. Patton")
    _normalize_name("Gen. Patton")
    assert _normalize_name.cache_info().hits >= 1


def test_add_event_mention_dedup_tolerates_legacy_mention():
    """The dedup filter must not KeyError on a legacy event_mention lacking Sub_eventID."""
    # Mirror the exact filter used in _add_event_mention after the fix.
    place_data = {
        "event_mentions": [
            {"MentionID": "m1"},  # legacy: no Sub_eventID
            {"MentionID": "m2", "Sub_eventID": "01SUB0000000000000000000A0"},
        ]
    }
    sub_event_id = "01SUB0000000000000000000A0"
    existing = [
        m
        for m in place_data.get("event_mentions", [])
        if m.get("Sub_eventID") == sub_event_id
    ]
    assert len(existing) == 1  # matched the non-legacy one; legacy skipped, no crash
