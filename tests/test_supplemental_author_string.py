"""Guard: a string ``citation.author`` must not drop the sub-event's supplemental materials.

XChannel ch8: all 9 sub-events of chapter 8b failed validation with "'Jones, Clifford' is not of
type 'array'" (validation errors are not retried) -> 0 materials from 102 endnotes. The prompt's own
example showed author as a string while the schema requires an array.
"""

from pathlib import Path

import yaml

from src.extraction import supplemental as sup


def _resp(author):
    return {
        "Sub-eventID": "01M6J3NGB3DR9QD7VT6ST9Y8SH",
        "Supplemental_Material": [
            {
                "classification": "citation",
                "prose_content": None,
                "citation": {"author": author, "title": "Cross-Channel Attack"},
            }
        ],
    }


def _authors(resp):
    return resp["Supplemental_Material"][0]["citation"]["author"]


def test_string_author_wrapped_verbatim_not_split():
    out = sup.sanitize_supplemental_data(
        _resp("Rapport, Leonard and Norwood, Arthur Jr.")
    )
    assert _authors(out) == ["Rapport, Leonard and Norwood, Arthur Jr."]


def test_list_author_unchanged_and_none_empty():
    assert _authors(sup.sanitize_supplemental_data(_resp(["A", "B"]))) == ["A", "B"]
    assert _authors(sup.sanitize_supplemental_data(_resp(None))) == []
    assert _authors(sup.sanitize_supplemental_data(_resp("   "))) == []


def test_coercion_is_observed(monkeypatch):
    seen = []
    monkeypatch.setattr(sup, "observe_warn", lambda *a, **k: seen.append(a))
    sup.sanitize_supplemental_data(_resp("Jones, Clifford"))
    assert ("supplemental", "author_coerced") in seen


def test_extract_with_retry_keeps_string_author_response(monkeypatch):
    """End to end through the real sanitize -> validate path (the one that dropped 9/9)."""

    class _Grok:
        def _get_model(self, _t):
            return "m"

        def extract_json(self, **_k):
            return _resp("Jones, Clifford")

    fn = sup._extract_with_retry
    import inspect

    params = inspect.signature(fn).parameters
    kwargs = {}
    for name in params:
        if name in ("grok_client",):
            kwargs[name] = _Grok()
        elif name == "prompt":
            kwargs[name] = "p"
        elif name == "sub_event_id":
            kwargs[name] = "01M6J3NGB3DR9QD7VT6ST9Y8SH"
    out = fn(**kwargs)
    assert out is not None
    assert _authors(out) == ["Jones, Clifford"]


def test_prompt_example_matches_schema_type():
    prompt = yaml.safe_load(
        Path("prompts/supplemental.yaml").read_text(encoding="utf-8")
    )
    assert '"author": ["Harrison, Gordon A."]' in prompt["schema"]
