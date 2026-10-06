"""Learned-alias store: Grok resolutions auto-persist per unique designation, so a later
(fresh-cache) resolve is deterministic + Grok-free. First-wins. Curated YAML untouched.
"""

import src.extraction.equipment_disambiguation as d


class CountingGrok:
    def __init__(self, canonical="T-34"):
        self.calls = 0
        self._c = canonical

    def extract_json(self, *a, **k):
        self.calls += 1
        return {
            "canonical_name": self._c,
            "nationality_of_origin": "USSR",
            "equivalents": ["T-34/76"],
            "confidence": 0.9,
        }


def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_LEARNED_PATH", tmp_path / "learned.yaml")
    monkeypatch.setattr(d, "_SUGGESTIONS_PATH", tmp_path / "sugg.jsonl")
    d._CACHE.clear()
    d._LEARNED_CACHE = None


def test_grok_resolution_persists_to_learned(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    g = CountingGrok()
    r = d.resolve_designation("T-34", g)
    assert r["identity_source"] == "grok_disambiguation" and g.calls == 1
    assert (tmp_path / "learned.yaml").exists()


def test_fresh_cache_serves_learned_not_grok(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    g = CountingGrok()
    d.resolve_designation("T-34", g)  # Grok once -> persisted
    d._CACHE.clear()  # simulate a fresh process (in-proc cache gone)
    d._LEARNED_CACHE = None  # reload learned from disk
    g2 = CountingGrok()
    r = d.resolve_designation("T-34", g2)
    assert r["identity_source"] == "learned_alias"
    assert g2.calls == 0  # NO repeat Grok call — deterministic + cheap
    assert r["canonical_name"] == "T-34"


def test_first_resolution_wins_stability(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    d.resolve_designation("T-34", CountingGrok("T-34"))  # first wins
    d._CACHE.clear()
    d._LEARNED_CACHE = None
    # even if Grok would now answer differently, the learned store is stable
    r = d.resolve_designation("T-34", CountingGrok("WRONG ANSWER"))
    assert r["canonical_name"] == "T-34"


def test_curated_yaml_never_modified(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    from src.extraction.equipment import _equipment_aliases

    before = dict(_equipment_aliases())
    d.resolve_designation("T-34", CountingGrok())
    assert dict(_equipment_aliases()) == before  # curated table unchanged
