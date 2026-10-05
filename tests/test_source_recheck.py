"""Reusable SourceRechecker engine — entity-agnostic."""

from src.extraction.source_recheck import (
    FieldRecheckSpec,
    SourceRechecker,
    default_source_text,
)


class _Grok:
    def __init__(self, r):
        self._r = r

    def extract_json(self, prompt, use_cache=True, cache_type=""):
        return self._r


def _spec():
    return FieldRecheckSpec(
        fields={"nationality": "alpha-3 or null", "role_type": "role or null"},
        needed=lambda r: [f for f in ("nationality", "role_type") if not r.get(f)],
        label="person",
    )


def test_fills_missing_fields_from_source():
    rec = {
        "name": "John Smith",
        "event_mentions": [{"original_text": "American general John Smith"}],
    }
    n = SourceRechecker(_spec()).recheck(
        rec, _Grok({"nationality": "USA", "role_type": "general"})
    )
    assert n == 2
    assert rec["nationality"] == "USA" and rec["role_type"] == "general"
    assert rec["_provenance"]["nationality"]["sourced_from"] == "original_text"


def test_gap_fill_only_and_null_ignored():
    rec = {
        "name": "X",
        "nationality": "USA",
        "event_mentions": [{"original_text": "text"}],
    }
    # only role_type is needed; grok returns null -> nothing filled, nationality kept
    n = SourceRechecker(_spec()).recheck(rec, _Grok({"role_type": None}))
    assert n == 0 and rec["nationality"] == "USA"


def test_noop_when_nothing_needed():
    rec = {"name": "X", "nationality": "USA", "role_type": "general"}
    assert SourceRechecker(_spec()).recheck(rec, _Grok({})) == 0


def test_fail_safe_on_error():
    class _Boom:
        def extract_json(self, **k):
            raise RuntimeError("down")

    rec = {"name": "X", "event_mentions": [{"original_text": "t"}]}
    assert SourceRechecker(_spec()).recheck(rec, _Boom()) == 0  # no crash


def test_default_source_text_from_mentions():
    rec = {"event_mentions": [{"original_text": "a"}, {"original_text": "b"}]}
    assert default_source_text(rec) == "a b"
    assert default_source_text({"original_text": "top"}) == "top"
