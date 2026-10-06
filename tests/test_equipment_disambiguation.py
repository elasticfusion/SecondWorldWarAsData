"""Canonical-lookup equipment disambiguator: exact->alias->fuzzy->Grok (cached),
provenance-stamped, fail-open, suggestions written. Canonical lookup only (no specs)."""

import tempfile
from pathlib import Path

import src.extraction.equipment_disambiguation as d
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA


class FakeGrok:
    def __init__(self, result=None):
        self.calls = 0
        self._r = result or {
            "canonical_name": "M4A2 Sherman",
            "nationality_of_origin": "USA",
            "equivalents": ["M4A2", "Sherman III"],
            "confidence": 0.9,
        }

    def extract_json(self, *a, **k):
        self.calls += 1
        return self._r


def setup_function(_):
    # Isolate ALL persisted state so Grok-path assertions aren't short-circuited by a
    # learned entry (and nothing is written to the real config/output dirs).
    d._CACHE.clear()
    d._LEARNED_CACHE = {}
    _tmp = Path(tempfile.mkdtemp())
    d._LEARNED_PATH = _tmp / "learned.yaml"
    d._SUGGESTIONS_PATH = _tmp / "sugg.jsonl"


def test_alias_short_circuits_grok():
    g = FakeGrok()
    r = d.resolve_designation("Sherman", g)
    assert r["identity_source"] == "alias" and g.calls == 0
    assert r["canonical_name"] == "m4 sherman"


def test_grok_fallback_resolves_and_stamps():
    g = FakeGrok()
    r = d.resolve_designation("Sherman V", g)
    assert r["identity_source"] == "grok_disambiguation"
    assert r["canonical_name"] == "M4A2 Sherman"
    assert r["nationality_of_origin"] == "USA"
    assert "Sherman III" in r["equivalents"]
    assert g.calls == 1


def test_cache_prevents_re_call():
    g = FakeGrok()
    d.resolve_designation("Sherman V", g)
    d.resolve_designation("Sherman V", g)
    assert g.calls == 1  # second call served from cache


def test_fail_open_on_grok_error():
    class Bad:
        def extract_json(self, *a, **k):
            raise RuntimeError("down")

    r = d.resolve_designation("Nonexistent Mk IX", Bad())
    assert r["identity_source"] == "raw"
    assert r["canonical_name"] == "Nonexistent Mk IX"


def test_no_grok_client_falls_through_to_raw():
    r = d.resolve_designation("Totally Unknown Thing", None)
    assert r["identity_source"] == "raw"


def test_suggestions_written(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_SUGGESTIONS_PATH", tmp_path / "sugg.jsonl")
    d.resolve_designation("Sherman V", FakeGrok())
    import json

    lines = (tmp_path / "sugg.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["raw"] == "Sherman V" and rec["canonical_name"] == "M4A2 Sherman"


def test_schema_declares_canonical_fields():
    props = EQUIPMENT_OUTPUT_SCHEMA["properties"]
    assert "canonical_name" in props and "identity_source" in props


def test_empty_input_returns_none():
    assert d.resolve_designation("", FakeGrok()) is None
