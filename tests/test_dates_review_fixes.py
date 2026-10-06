"""Dates end-to-end review fixes: year-precision resolution, no false UTC Z, HH:MM
validation, autumn==fall dedup, normalized_datetime guarded to exact ISO, DateMentionID.
"""

from src.extraction.date_resolution import resolve_date_interval as R
from src.extraction.dates import (
    _normalize_date_key,
    _build_normalized_datetime,
    _add_event_mention,
)


def test_early_mid_late_of_year_resolve():
    assert R("early-1944") == (
        "1944-01-01T00:00:00",
        "1944-04-30T23:59:59",
        "precision_rule",
    )
    assert R("mid-1944") == (
        "1944-05-01T00:00:00",
        "1944-08-31T23:59:59",
        "precision_rule",
    )
    assert R("late-1944") == (
        "1944-09-01T00:00:00",
        "1944-12-31T23:59:59",
        "precision_rule",
    )


def test_no_false_utc_suffix():
    lo, hi, _ = R("1945-01-05", None, "05:00")
    assert not lo.endswith("Z") and not hi.endswith(
        "Z"
    )  # naive — time_source not applied


def test_malformed_time_falls_back_to_day_bounds():
    assert R("1945-01-05", None, "5pm") == (
        "1945-01-05T00:00:00",
        "1945-01-05T23:59:59",
        "precision_rule",
    )
    assert R("1945-01-05", None, "25:99") == (
        "1945-01-05T00:00:00",
        "1945-01-05T23:59:59",
        "precision_rule",
    )


def test_autumn_and_fall_share_dedup_key():
    assert _normalize_date_key("autumn-1944") == _normalize_date_key("fall-1944")
    assert _normalize_date_key("fall-1944") == "1944-fall"


def test_normalized_datetime_only_for_exact_iso():
    assert (
        _build_normalized_datetime({"date_start": "1944-06-06"})
        == "1944-06-06T00:00:00Z"
    )
    assert _build_normalized_datetime({"date_start": "1944-06"}) == "1944-06T00:00:00Z"
    # approximate -> None, NOT 'summer-1944T00:00:00Z'
    assert _build_normalized_datetime({"date_start": "summer-1944"}) is None
    assert _build_normalized_datetime({"date_start": "early-1944-06"}) is None


def test_new_mentions_use_datementionid(tmp_path):
    import json

    f = tmp_path / "d.json"
    f.write_text(
        json.dumps(
            {
                "DateID": "01ABCDEFGH0123456789ABCDEF",
                "date_start": "1945-01-05",
                "event_mentions": [],
            }
        )
    )
    _add_event_mention(
        f,
        {"original_text": "t"},
        "E",
        "01EV0000000000000000000000",
        "SE",
        "01SE0000000000000000000000",
        "B",
        "A",
        "S",
    )
    m = json.loads(f.read_text())["event_mentions"][0]
    assert "DateMentionID" in m and "MentionID" not in m
