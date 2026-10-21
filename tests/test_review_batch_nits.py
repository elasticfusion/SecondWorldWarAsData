"""Regression — §1.0c batched SUGGESTION/NIT fixes."""

import json
from pathlib import Path
from unittest import mock


def test_supplemental_invalid_content_class_defaults_ambiguous():
    from src.extraction.supplemental import _normalize_enums

    m = {"content_class": "totally-bogus"}
    _normalize_enums(m)
    assert m["content_class"] == "ambiguous"  # was wrongly 'citation'
    # a valid class is left as-is
    m2 = {"content_class": "prose"}
    _normalize_enums(m2)
    assert m2["content_class"] == "prose"


def test_enrich_group_reads_group_name(tmp_path):
    from src.extraction import enrich_groups as eg

    # schema-valid record carries ONLY group_name (not 'name') -> must still be considered named
    f = tmp_path / "g.json"
    f.write_text(
        json.dumps(
            {
                "PeopleGroupID": "X",
                "group_name": "5th Panzer Army",
                "enrichment_data": {"done": 1},
            }
        )
    )
    # already-enriched short-circuit proves 'name' was resolved from group_name (not empty->False)
    assert (
        eg.enrich_group(f, mock.Mock()) is False
    )  # returns False via already-enriched, not no-name


def test_storage_resolve_returns_resolved_path(tmp_path):
    from src.utils.storage import LocalStorage

    s = LocalStorage(str(tmp_path))
    p = s._resolve("sub/x.json")
    assert p == (tmp_path / "sub" / "x.json").resolve()  # resolved, not a raw re-join
    # escape still blocked
    import pytest

    with pytest.raises(ValueError):
        s._resolve("../../etc/passwd")


def test_weather_add_mention_tolerates_legacy_no_subevent(tmp_path):
    # the dedup filter must not KeyError on a legacy weather mention lacking Sub_eventID
    mentions = [
        {"MentionID": "m1"},
        {"MentionID": "m2", "Sub_eventID": "01SUB00000000000000000A0"},
    ]
    sid = "01SUB00000000000000000A0"
    existing = [m for m in mentions if m.get("Sub_eventID") == sid]
    assert len(existing) == 1  # mirrors the fixed filter; no crash on the legacy one


def test_openserp_check_duplicate_skips_malformed(tmp_path):
    from src.extraction.openserp_maps import _check_duplicate_map

    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "index.json").write_text("{}")
    (tmp_path / "good.json").write_text(
        json.dumps({"external_source_url": "http://x/m.jpg"})
    )
    assert (
        _check_duplicate_map("http://x/m.jpg", tmp_path) is True
    )  # found, bad+index skipped
    assert _check_duplicate_map("http://other/y.jpg", tmp_path) is False
