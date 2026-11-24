"""What a map shows, kept on its Map record (map_depicted): cleaned model output, provenance, the
legibility gate (M7) and the never-ask-twice marker."""

from src.extraction import map_depicted as md


def test_only_known_fields_are_kept_and_capped():
    out = md.clean(
        {
            "units": ["28th Infantry Division", "", 5, "x" * 900],
            "places": "Schmidt",  # not a list: dropped
            "description": "  Tactical map near Schmidt.  ",
            "legibility_confidence": "0.8",
            "script": "<script>",
        }
    )
    assert out == {
        "units": ["28th Infantry Division", "x" * 500],
        "description": "Tactical map near Schmidt.",
        "legibility_confidence": 0.8,
    }


def test_nothing_usable_is_none():
    assert (
        md.clean({}) is None
        and md.clean("x") is None
        and md.clean({"units": []}) is None
    )


def test_the_legibility_gate():
    assert md.is_legible({"legibility_confidence": 0.85})
    assert md.is_legible({"legibility_confidence": md.MIN_LEGIBILITY_CONFIDENCE})
    assert not md.is_legible({"legibility_confidence": 0.15})
    assert md.is_legible({})  # no stated confidence: not gated
    assert md.is_legible({"legibility_confidence": "high"})  # unparseable: not gated
    assert md.legibility_confidence({"legibility_confidence": "0.4"}) == 0.4


def test_apply_and_was_read():
    rec = {"MapID": "01HX7YZABCDEFGHJKMNPQRSTVW"}
    assert not md.was_read(rec)
    prov = md.provenance(
        md.NOT_RELEVANT,
        kind="k",
        model=None,
        request_id=None,
        image_url="u",
        found_in="b",
    )
    md.apply(rec, {"units": ["x"]}, prov)
    assert rec["depicted"] is None and md.was_read(rec)  # not relevant: nothing kept


def test_the_request_id_is_taken_only_when_echoed():
    assert md.answer_request_id({"RequestID": "abc"}) == "abc"
    assert md.answer_request_id({"RequestID": 5}) is None
    assert md.answer_request_id(None) is None


def test_openserp_maps_keep_what_they_show_and_no_longer_recycle():
    import inspect

    import src.extraction.openserp_maps as om

    src = inspect.getsource(om._process_search_result)
    assert "map_depicted.apply" in src and "recycle_map_as_source" not in src


def test_a_confidence_that_is_not_a_finite_number_is_not_kept():
    for raw in (float("nan"), "1e999", True, "x"):
        assert md.legibility_confidence({"legibility_confidence": raw}) is None
    assert md.legibility_confidence({"legibility_confidence": 7}) == 1.0  # clamped
    assert md.legibility_confidence({"legibility_confidence": -1}) == 0.0


def test_the_verdict_follows_relevance_and_usable_contents():
    assert (
        md.verdict_for({"is_relevant": True, "depicted": {"units": ["x"]}})
        == md.DEPICTED
    )
    assert (
        md.verdict_for({"is_relevant": False, "depicted": {"units": ["x"]}})
        == md.NOT_RELEVANT
    )
    assert md.verdict_for({"is_relevant": True, "depicted": {}}) == md.NOT_RELEVANT
    assert md.verdict_for(None) == md.NOT_RELEVANT


def test_a_malformed_index_entry_is_not_a_record(tmp_path):
    from src.extraction import map_link_source as mls

    (tmp_path / "index.json").write_text("{}")
    for name in (5, "index.json", "../x.json", "a/b.json", "m.txt"):
        assert mls._map_file(tmp_path, {"u": name}, {"url": "u"}) is None
    assert mls._map_file(tmp_path, ["not a dict"], {"url": "u"}) is None  # type: ignore[arg-type]
