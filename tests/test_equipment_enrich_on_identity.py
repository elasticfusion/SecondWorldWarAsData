"""Enrichment follows identity resolution: specific records get enriched once; generic
records skipped; never re-enriched; auto-created stubs (Pershing) enriched. image_scope
defaults to representative."""

from pathlib import Path

from src.extraction.equipment import (
    MediaItem,
    _is_specific_identity,
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
    assert _is_specific_identity({"technical_identifier": "M4"})
    assert _is_specific_identity({"common_name": "M4 Sherman"})
    assert _is_specific_identity({"common_name": "M26 Pershing"})
    assert not _is_specific_identity({"common_name": "tank"})
    assert not _is_specific_identity({"common_name": "machine guns"})
    assert not _is_specific_identity({"common_name": ""})


def test_generic_record_not_enriched(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_and_add_media",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {"common_name": "tank"}
    _enrich_on_identity(data, _Grok(), False, None, None)
    assert hit["n"] == 0
    assert "enrichment_status" not in data


def test_specific_record_enriched_and_stamped(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_and_add_media",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {
        "common_name": "M4 Sherman",
        "technical_identifier": "M4",
        "category": "armor",
    }
    _enrich_on_identity(data, _Grok(), False, None, None)
    assert hit["n"] == 1
    assert data["enrichment_status"] == "enriched"


def test_never_re_enriched(monkeypatch):
    import src.extraction.equipment as eqmod

    hit = {"n": 0}
    monkeypatch.setattr(
        eqmod,
        "_enrich_and_add_media",
        lambda *a, **k: hit.__setitem__("n", hit["n"] + 1),
    )
    data = {"common_name": "M4 Sherman", "enrichment_status": "enriched"}
    _enrich_on_identity(data, _Grok(), False, None, None)
    assert hit["n"] == 0  # already stamped -> skipped


def test_autocreated_pershing_stub_enriched(monkeypatch, tmp_path):
    import src.extraction.equipment as eqmod

    seen = {}

    def fake_enrich(data, *a, **k):
        seen["name"] = data["common_name"]
        data["category"] = "armor"

    monkeypatch.setattr(eqmod, "_enrich_and_add_media", fake_enrich)
    idx = {}
    eid = _autocreate_minimal_equipment("M26 Pershing", idx, tmp_path, _Grok(), False)
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
