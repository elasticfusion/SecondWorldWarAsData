"""PATTERN 4 (silent drops) regressions — Phase-2 review.

Each over-strict filter / swallowed-error that produced silent 0s is now (a) relaxed to stop the
drop and (b) emits an OBSERV count so the behavior is visible.
"""

import json


# --- #1 dates year-veto: advisory, not a veto -----------------------------------------------
def test_dates_keeps_out_of_window_year_drops_only_garbage():
    from src.extraction.dates import _is_valid_date_mention

    base = {"date_start": "1944-06-06", "original_text": "6 June 1944"}
    assert _is_valid_date_mention(base) is True
    # out-of-WWII-window but legit (WWI background / publication date) -> KEPT (was dropped)
    assert (
        _is_valid_date_mention(
            {"date_start": "1918-11-11", "original_text": "Armistice"}
        )
        is True
    )
    assert (
        _is_valid_date_mention(
            {"date_start": "1956-09-12", "original_text": "interview, 12 Sep 1956"}
        )
        is True
    )
    # clearly-garbage year -> dropped
    assert (
        _is_valid_date_mention(
            {"date_start": "0231-01-01", "original_text": "ocr noise"}
        )
        is False
    )


def test_make_date_key_keeps_out_of_window():
    from src.extraction.batch_parallel import _make_date_key

    assert _make_date_key({"date_start": "1918-11-11"}) != ""  # kept (was rejected)
    assert _make_date_key({"date_start": "0101-01-01"}) == ""  # garbage rejected


# --- #2 casualties keyword pre-filter broadened --------------------------------------------
def test_casualty_mention_catches_narrative_language():
    from src.extraction.casualties import _has_casualty_mention

    for t in (
        "the battalion suffered heavy losses",
        "many men fell in the assault",
        "the company was wiped out",
        "the platoon was annihilated",
        "the unit was decimated and overran",
    ):
        assert _has_casualty_mention(t) is True, t
    assert _has_casualty_mention("the weather was clear and cold") is False


# --- #3 people_groups wrapper-key variants + bare list -------------------------------------
def test_parse_groups_accepts_wrapper_variants_and_bare_list():
    from src.extraction.people_groups import _parse_groups_response

    assert _parse_groups_response('{"People_Groups": [{"a": 1}]}') == [{"a": 1}]
    assert _parse_groups_response('{"groups": [{"b": 2}]}') == [{"b": 2}]  # variant key
    assert _parse_groups_response('[{"c": 3}]') == [{"c": 3}]  # bare list
    assert (
        _parse_groups_response('{"unexpected": [{"d": 4}]}') == []
    )  # unknown -> [] (observed)


# --- #4 supplemental _append_to_file dedups on identity (narrative not re-appended) --------
def test_append_to_file_dedups_narrative_subevents(tmp_path):
    from src.extraction.supplemental import _append_to_file

    f = tmp_path / "ch1-notes-event.json"
    sub = {
        "Sub-eventID": "01HZXSAAAAAAAAAAAAAAAAAAAA",
        "Sub-event_summary": "a narrative note",  # NO reference_source (the old dedup key)
    }
    f.write_text(
        json.dumps(
            {"Event": {"EventID": "01HZXAAAAAAAAAAAAAAAAAAAAA", "Sub-events": [sub]}}
        ),
        encoding="utf-8",
    )
    # Re-appending the SAME narrative sub-event must add nothing (old code re-added every pass).
    _append_to_file(f, [dict(sub)], "notes")
    data = json.loads(f.read_text())
    assert len(data["Event"]["Sub-events"]) == 1
    # a genuinely new one IS added
    _append_to_file(
        f,
        [{"Sub-eventID": "01HZXSBBBBBBBBBBBBBBBBBBBB", "Sub-event_summary": "new"}],
        "notes",
    )
    assert len(json.loads(f.read_text())["Event"]["Sub-events"]) == 2


# --- #5 _has_document keeps the hybrid citation half ---------------------------------------
def test_has_document_hybrid_keeps_citation():
    from src.extraction.supplemental import _has_document

    # HYBRID: real verbatim citation AND prose -> now TRUE (citation half stored; was dropped)
    assert (
        _has_document(
            {
                "verbatim_reference": "FUSA AAR, Jun 1944, RG 407",
                "prose_content": "The corps advanced rapidly.",
            }
        )
        is True
    )
    # pure prose, no citation -> still False (no bogus bibliography record)
    assert _has_document({"prose_content": "The corps advanced."}) is False
    # trivial verbatim fragment -> False (length floor)
    assert _has_document({"verbatim_reference": "ibid"}) is False


# --- #6 supplemental_info_pipeline: temp event.json path is contained (no prefix traversal) -----
def test_supplemental_info_tempfile_prefix_cannot_escape(tmp_path, monkeypatch):
    from src.extraction import supplemental_info_pipeline as sip

    seen = {}

    def _fake_extract_dates(event_file, grok_client, dates_dir):
        # the extractor receives a PATH (not the old dict) and it must live under the temp dir
        seen["event_file"] = event_file
        return None

    monkeypatch.setattr(
        "src.extraction.dates.extract_dates", _fake_extract_dates, raising=True
    )
    pseudo = {
        "Event": {
            "EventID": "01HZXAAAAAAAAAAAAAAAAAAAAA",
            "Sub-events": [{"Sub-eventID": "../../../../etc/evil"}],  # hostile
        }
    }
    sip._extract_entity_type("dates", pseudo, object(), tmp_path, {})
    ef = seen["event_file"]
    # the written temp file stays contained under tmp_path/_supplemental_tmp (prefix sanitized)
    assert str(ef.resolve()).startswith(str((tmp_path / "_supplemental_tmp").resolve()))
    assert not (tmp_path.parent / "etc" / "evil").exists()


# --- #7 people_consolidation save is atomic + returns bool ---------------------------------
def test_save_consolidated_is_atomic_and_returns_true(tmp_path):
    from src.extraction.people_consolidation import _save_consolidated_data

    out = tmp_path / "people-consolidated.json"
    ok = _save_consolidated_data(out, [{"PersonID": "01P", "name": "X"}])
    assert ok is True
    assert json.loads(out.read_text())["People"][0]["name"] == "X"
    # no stale tempfile left behind
    assert not list(tmp_path.glob("*.tmp"))
