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
    assert ("supplemental", "citation_coerced") in seen


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


def _cite(**kw):
    r = _resp(["A"])
    r["Supplemental_Material"][0]["citation"].update(kw)
    return r


def _c(resp):
    return resp["Supplemental_Material"][0]["citation"]


def test_numeric_and_list_string_fields_coerced_and_validate():
    out = sup.sanitize_supplemental_data(
        _cite(publication_date=1951, volume=2, pages=120, isbn=["0-1", "0-2"])
    )
    c = _c(out)
    assert (
        c["publication_date"] == "1951" and c["volume"] == "2" and c["pages"] == "120"
    )
    assert c["isbn"] == "0-1" and c["isbn_edition"] == "0-2"
    sup.validate_supplemental_json(out)  # must not raise


def test_prompt_key_aliases_mapped():
    c = _c(
        sup.sanitize_supplemental_data(_cite(ISBN="0-1", location="DC", country="USA"))
    )
    assert c["isbn"] == "0-1" and c["publication_location"] == "DC"
    assert c["publication_country"] == "USA" and "ISBN" not in c


def test_dirty_author_list_cleaned():
    assert _authors(sup.sanitize_supplemental_data(_resp(["", None, "Jones", 3]))) == [
        "Jones",
        "3",
    ]


def test_prompt_example_keys_exist_in_schema():
    import json

    prompt = yaml.safe_load(
        Path("prompts/supplemental.yaml").read_text(encoding="utf-8")
    )
    ex = json.loads(prompt["schema"])["Supplemental_Material"][0]["citation"]
    props = sup.SUPPLEMENTAL_SCHEMA["properties"]["Supplemental_Material"]["items"][
        "properties"
    ]["citation"]["properties"]
    assert [k for k in ex if k not in props] == []


def test_non_scalar_values_never_drop_the_sub_event():
    out = sup.sanitize_supplemental_data(
        _cite(
            pages=[None, "12"],
            volume=[["x"]],
            edition={"n": 2},
            publication_date=1951.0,
        )
    )
    c = _c(out)
    assert c["pages"] == "12" and c["volume"] is None and c["edition"] is None
    assert c["publication_date"] == "1951"
    sup.validate_supplemental_json(out)


def test_alias_conflict_is_reported(monkeypatch):
    seen = []
    monkeypatch.setattr(sup, "observe_warn", lambda *a, **k: seen.append(k))
    c = _c(sup.sanitize_supplemental_data(_cite(ISBN="X", isbn="Y")))
    assert c["isbn"] == "Y"
    assert "ISBN->isbn(conflict_dropped)" in seen[0]["fields"]


def test_observ_carries_response_sub_event_id(monkeypatch):
    seen = []
    monkeypatch.setattr(sup, "observe_warn", lambda *a, **k: seen.append(k))
    sup.sanitize_supplemental_data(_resp("Jones, Clifford"))
    assert seen[0]["sub_event"] == "01M6J3NGB3DR9QD7VT6ST9Y8SH"


def test_non_string_title_and_nan_are_safe():
    out = sup.sanitize_supplemental_data(_cite(title=1944, pages=float("nan")))
    c = _c(out)
    assert c["title"] == "1944" and c["pages"] is None
    sup.validate_supplemental_json(out)
