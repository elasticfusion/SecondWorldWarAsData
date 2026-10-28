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
