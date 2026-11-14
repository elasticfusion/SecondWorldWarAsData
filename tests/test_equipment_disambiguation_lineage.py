"""The disambiguator says what a variant is a variant of and what sets it apart (owner,
2026-11-14: "tell us what makes the M4A1 distinct from the M4"), so records of one family
consolidate (Sherman > M4 > M4A1) and the difference is stated on the record."""

from unittest import mock

import pytest

from src.extraction import equipment_disambiguation as D

ANSWER = {
    "canonical_name": "M4A1 Sherman",
    "nationality_of_origin": "USA",
    "equivalents": ["M4A1", "Sherman II"],
    "variant_of": "M4 Sherman",
    "distinguishing_features": "Cast rather than welded upper hull.",
    "confidence": 0.95,
}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_LEARNED_PATH", tmp_path / "learned.yaml")
    monkeypatch.setattr(D, "_SUGGESTIONS_PATH", tmp_path / "suggestions.jsonl")
    monkeypatch.setattr(D, "_LEARNED_CACHE", None, raising=False)
    monkeypatch.setattr(D, "_CACHE", {})
    monkeypatch.setattr(D, "_aliases", lambda: {})
    monkeypatch.setenv("EQUIPMENT_DISAMBIGUATION", "true")


def _client(answer):
    client = mock.Mock()
    client.extract_json.return_value = answer
    client._get_model.return_value = "m"
    return client


def test_a_variant_carries_its_base_type_and_difference(isolated):
    out = D.resolve_designation("M4A1", _client(ANSWER))
    assert out["variant_of"] == "M4 Sherman"
    assert out["distinguishing_features"] == "Cast rather than welded upper hull."
    rendered = _client(ANSWER)
    D._CACHE.clear()
    D.resolve_designation("Sherman II", rendered)
    prompt = rendered.extract_json.call_args.args[0]
    assert "variant_of" in prompt and "distinguishing_features" in prompt


def test_the_lineage_is_learned_and_served_without_grok(isolated):
    D.resolve_designation("M4A1", _client(ANSWER))
    D._CACHE.clear()
    silent = _client(None)
    out = D.resolve_designation("M4A1", silent)
    silent.extract_json.assert_not_called()
    assert out["identity_source"] == "learned_alias"
    assert out["variant_of"] == "M4 Sherman"


def test_an_entry_learned_before_the_lineage_is_asked_once_and_upgraded(isolated):
    import yaml

    D._LEARNED_PATH.write_text(
        yaml.safe_dump({"learned": {"m4a1": {"canonical_name": "M4A1 Sherman"}}})
    )
    D._LEARNED_CACHE = None
    client = _client(dict(ANSWER, canonical_name="something else"))
    out = D.resolve_designation("M4A1", client)
    assert client.extract_json.call_count == 1 and out["variant_of"] == "M4 Sherman"
    assert out["canonical_name"] == "M4A1 Sherman"  # first-wins: what later runs serve
    learned = yaml.safe_load(D._LEARNED_PATH.read_text())["learned"]["m4a1"]
    assert (
        learned["canonical_name"] == "M4A1 Sherman"
    )  # the identity is kept (first wins)
    assert learned["variant_of"] == "M4 Sherman"


def test_a_base_type_has_no_lineage(isolated):
    out = D.resolve_designation(
        "M4", _client(dict(ANSWER, variant_of=None, distinguishing_features=""))
    )
    assert out["variant_of"] is None and out["distinguishing_features"] is None


def test_the_record_is_stamped(isolated, monkeypatch):
    from src.extraction import equipment as eq

    monkeypatch.setattr(
        D,
        "resolve_designation",
        lambda name, client=None: dict(ANSWER, identity_source="grok_disambiguation"),
    )
    record = {"common_name": "M4A1"}
    eq._resolve_canonical_identity(record, None)
    assert record["variant_of"] == "M4 Sherman"
    assert record["distinguishing_features"].startswith("Cast")


def test_a_legacy_entry_without_grok_is_served_not_downgraded(isolated, monkeypatch):
    import yaml

    D._LEARNED_PATH.write_text(
        yaml.safe_dump({"learned": {"m4a1": {"canonical_name": "M4A1 Sherman"}}})
    )
    D._LEARNED_CACHE = None
    out = D.resolve_designation("M4A1")  # no client (casualties, logistics)
    assert out["identity_source"] == "learned_alias"
    assert out["canonical_name"] == "M4A1 Sherman" and out["variant_of"] is None
    assert "m4a1" not in D._CACHE  # a later call with a client can still upgrade it
    monkeypatch.setenv("EQUIPMENT_DISAMBIGUATION", "false")
    assert (
        D.resolve_designation("M4A1", _client(ANSWER))["identity_source"]
        == "learned_alias"
    )
    monkeypatch.setenv("EQUIPMENT_DISAMBIGUATION", "true")
    assert D.resolve_designation("M4A1", _client(ANSWER))["variant_of"] == "M4 Sherman"


def test_a_base_type_named_as_its_own_variant_has_no_lineage(isolated):
    out = D.resolve_designation(
        "M4",
        _client(dict(ANSWER, canonical_name="M4 Sherman", variant_of="m4  sherman")),
    )
    assert out["variant_of"] is None and out["lineage_source"] is None


def test_the_lineage_carries_its_source(isolated):
    out = D.resolve_designation("M4A1", _client(ANSWER))
    assert out["lineage_source"] == "grok_disambiguation"
