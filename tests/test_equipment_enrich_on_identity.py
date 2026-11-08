"""Enrichment follows identity resolution: specific records get enriched once; generic
records are stamped generic and skipped (kept in the JSON, never searched); never re-enriched;
auto-created stubs (Pershing) enriched. image_scope defaults to representative."""

from pathlib import Path

from src.extraction.equipment import (
    MediaItem,
    _identity_scope,
    _enrich_on_identity,
    _autocreate_minimal_equipment,
)


class _Grok:
    """Stub that records whether enrichment was invoked."""

    def __init__(self):
        self.called = 0

    def chat_completion(self, *a, **k):
        self.called += 1
        return "{}"

    def extract_json(self, *a, **k):
        self.called += 1
        return {}


def test_specific_identity_gate():
    grok = _Grok()  # returns {} -> the canonical resolver finds nothing
    assert _identity_scope({"common_name": "M4 Sherman"}, grok) == "specific"
    assert _identity_scope({"common_name": "M26 Pershing"}, grok) == "specific"
    assert _identity_scope({"common_name": "tank"}, grok) == "generic"
    assert _identity_scope({"common_name": "machine guns"}, grok) == "generic"
    assert _identity_scope({"common_name": ""}, grok) == "generic"
    # technical_identifier is NOT evidence: the extractor fills it with the bare name
    assert (
        _identity_scope(
            {"common_name": "trench", "technical_identifier": "trench"}, grok
        )
        == "generic"
    )


def test_generic_record_not_enriched(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_from_encyclopedias",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {"common_name": "tank"}
    _enrich_on_identity(data, _Grok())
    assert hit["n"] == 0
    assert "enrichment_status" not in data
    assert data["identity_scope"] == "generic"  # kept in the JSON, never searched


def test_specific_record_enriched_and_stamped(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_from_encyclopedias",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {
        "common_name": "M4 Sherman",
        "technical_identifier": "M4",
        "category": "armor",
    }
    _enrich_on_identity(data, _Grok())
    assert hit["n"] == 1
    assert data["enrichment_status"] == "enriched"


def test_never_re_enriched(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_from_encyclopedias",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {"common_name": "M4 Sherman", "enrichment_status": "enriched"}
    _enrich_on_identity(data, _Grok())
    assert hit["n"] == 0  # already stamped -> skipped


def test_autocreated_pershing_stub_enriched(monkeypatch, tmp_path):
    import src.extraction.equipment as eqmod

    seen = {}

    def fake_enrich(data, *a, **k):
        seen["name"] = data["common_name"]
        data["category"] = "armor"

    monkeypatch.setattr(eqmod, "_enrich_from_encyclopedias", fake_enrich)
    idx = {}
    eid = _autocreate_minimal_equipment("M26 Pershing", idx, tmp_path, _Grok())
    assert eid and seen["name"] == "M26 Pershing"  # stub was enriched on creation
    import json

    rec = json.loads(Path(idx["M26 Pershing"]).read_text())
    assert rec["enrichment_status"] == "enriched" and rec["category"] == "armor"


def test_image_scope_defaults_representative():
    m = MediaItem(media_type="photo", url="http://x/img.jpg", source="commons")
    assert m.image_scope == "representative"
    m2 = MediaItem(
        media_type="photo",
        url="http://x/i.jpg",
        source="book",
        image_scope="documentary",
    )
    assert m2.image_scope == "documentary"
