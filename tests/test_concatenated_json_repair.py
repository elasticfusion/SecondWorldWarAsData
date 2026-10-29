"""Grok returns concatenated {...}{...} per sub-event -> 'Extra data'; the people batch zeroed
82->0 on exactly this (XChannel-8). _try_repair_json must merge concatenated objects."""

from src.grok_client import GrokClient


def _c():
    return GrokClient.__new__(GrokClient)


def test_merge_two_concatenated_people_objects():
    c = _c()
    resp = '{"people": [{"name": "J. M. Stagg"}]}{"people": [{"name": "Alfred Jodl"}]}'
    m = c._merge_concatenated_json(resp)
    assert [p["name"] for p in m["people"]] == ["J. M. Stagg", "Alfred Jodl"]


def test_merge_whitespace_separated():
    c = _c()
    resp = '{"people":[{"name":"A"}]}\n{"people":[{"name":"B"}]}\n{"people":[{"name":"C"}]}'
    assert len(c._merge_concatenated_json(resp)["people"]) == 3


def test_single_object_returns_none():
    # nothing to merge -> None so the normal parse path owns it
    assert _c()._merge_concatenated_json('{"people":[{"name":"A"}]}') is None


def test_garbage_is_failsafe_none():
    assert _c()._merge_concatenated_json("not json") is None


def test_repair_path_triggers_on_extra_data():
    c = _c()
    resp = '{"people":[{"name":"A"}]}{"people":[{"name":"B"}]}'
    out = c._try_repair_json(resp, "Extra data: line 1 column 26 (char 25)")
    assert out is not None and len(out["people"]) == 2


def test_repair_path_ignores_non_extra_data_non_escape():
    # unrelated error + not escape -> no concat attempt, returns None (unchanged behavior)
    assert _c()._try_repair_json('{"a":1} {"a":2}', "Expecting value") is None


def test_merge_preserves_multiple_keys():
    c = _c()
    resp = '{"people":[{"n":"A"}],"meta":1}{"people":[{"n":"B"}]}'
    m = c._merge_concatenated_json(resp)
    assert len(m["people"]) == 2 and m["meta"] == 1


def test_valid_then_truncated_recovers_valid_object():
    """The groups=0 fail_stop (2026-10-29): Grok returned a VALID {groups:[...]} object followed
    by a TRUNCATED second object. #616 discarded EVERYTHING (whole groups batch lost). The resilient
    fix must recover the valid first object instead of returning None."""
    c = GrokClient.__new__(GrokClient)
    resp = (
        '{"groups":[{"sub_event_id":"S1","groups":[{"name":"VII Corps"},'
        '{"name":"5th Armored Division"}]}]}'
        '{"groups":[{"sub_event_id":"S2","groups":[{"name":"9th Infantry"'  # truncated
    )
    m = c._merge_concatenated_json(resp)
    assert m is not None, "must recover the valid object, not discard all"
    assert len(m["groups"]) == 1


def test_valid_then_prose_tail_recovers():
    c = GrokClient.__new__(GrokClient)
    m = c._merge_concatenated_json('{"groups":[{"x":1}]} some trailing prose')
    assert m is not None and m["groups"] == [{"x": 1}]


def test_single_clean_object_still_falls_through():
    # a single clean object is the NORMAL path -> None so the caller's json.loads owns it
    c = GrokClient.__new__(GrokClient)
    assert c._merge_concatenated_json('{"groups":[{"x":1}]}') is None
