"""M1: in-document map links -> what each map shows, kept on its Map record (map_depicted)."""

import json
from pathlib import Path

import pytest

import src.extraction.map_link_source as MLS


# --- parser relative-link resolution (Task 1) ---
def test_extract_maps_resolves_relative_against_base():
    from src.parser import extract_maps

    base = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"
    out = extract_maps(
        "See [Map VIII](maps/USA-E-XChannel-VIII.jpg) for the drop.", base_url=base
    )
    assert out == [
        (
            "VIII",
            "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/maps/USA-E-XChannel-VIII.jpg",
        )
    ]


def test_extract_maps_absolute_unchanged_and_relative_needs_base():
    from src.parser import extract_maps

    assert extract_maps("[Map VII](https://x/y/v.jpg)") == [
        ("VII", "https://x/y/v.jpg")
    ]
    assert extract_maps("[Map VIII](maps/x.jpg)") == []  # no base -> not absolutized
    # non-asset relative link ignored even with base
    assert (
        extract_maps("[Map Note](USA-E-XChannel-9.html)", base_url="https://x/a/b.html")
        == []
    )


# --- host allowlist ---
def test_host_allowed():
    assert MLS._host_allowed("https://www.ibiblio.org/x/maps/m.jpg") is True
    assert MLS._host_allowed("https://evil.example/m.jpg") is False
    # The uploaded document's OWN host is not trusted (its URL is uploader-supplied metadata);
    # only ibiblio and operator-configured hosts are.
    assert MLS._host_allowed("https://cdn.example/m.jpg") is False
    assert MLS._host_allowed("https://cdn.example/m.jpg", ("cdn.example",)) is True
    assert MLS._host_allowed("https://x.cdn.example/m.jpg", ("cdn.example",)) is True
    assert MLS._host_allowed("https://notcdn.example/m.jpg", ("cdn.example",)) is False


# --- read: vision -> what the map shows, on the Map record ---
_URL = "https://www.ibiblio.org/x/maps/VIII.jpg"
_DEPICTED = {
    "units": ["101st Airborne"],
    "places": ["Ste. Mère-Église"],
    "description": "Airborne drop zones map.",
    "legibility_confidence": 0.9,
}


class _GC:
    def __init__(self, answer=None):
        self.asked = 0
        self.answer = answer or {
            "is_relevant": True,
            "depicted": dict(_DEPICTED),
        }

    def extract_json_with_image(self, **k):
        self.asked += 1
        return self.answer  # like the real client: the echoed id is checked and removed

    def vision_request_id(self, prompt, image_url, kind):
        return "abcdef0123456789"  # the id the request was asked with (provenance)

    def _get_model(self, kind):
        return "grok-vision-test"


def _docs(url=_URL, book="XChannel"):
    return [
        {
            "book": book,
            "chapter_title": "The Sixth of June",
            "maps": [{"url": url, "map_id": "VIII", "description": "Map VIII"}],
        }
    ]


@pytest.fixture
def maps_dir(tmp_path, monkeypatch):
    """A Map record for _URL (as persist_map_entities makes it), the SSRF gate passed."""
    import json

    monkeypatch.setattr(MLS, "_ssrf_safe_image_url", lambda u: u)
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr(SEM, "_build_verification_prompt", lambda *a, **k: "PROMPT")
    d = tmp_path / "maps"
    d.mkdir()
    rec = {
        "MapID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "map_title": "Map VIII",
        "source_url": _URL,
    }
    (d / "m.json").write_text(json.dumps(rec))
    (d / "index.json").write_text(json.dumps({_URL: "m.json"}))
    return d


def _record(d):
    import json

    return json.loads((d / "m.json").read_text())


def test_what_a_map_shows_is_kept_on_its_record_with_provenance(maps_dir):
    out = MLS.read_in_doc_maps(_docs(), _GC(), maps_dir)
    assert out["read"] == 1 and out["depicted"] == 1
    rec = _record(maps_dir)
    assert rec["depicted"]["units"] == ["101st Airborne"]
    prov = rec["depicted_provenance"]
    assert prov["verdict"] == "depicted" and prov["kind"] == "in_doc_map_vision"
    assert (
        prov["request_id"] == "abcdef0123456789" and prov["model"] == "grok-vision-test"
    )
    assert prov["image_url"] == _URL and prov["found_in"] == "XChannel"
    from src.schemas.maps_output import MAPS_OUTPUT_SCHEMA
    import jsonschema

    jsonschema.validate(rec, MAPS_OUTPUT_SCHEMA)


def test_nothing_is_written_to_contentrepository(maps_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    MLS.read_in_doc_maps(_docs(), _GC(), maps_dir)
    assert not (tmp_path / "contentrepository").exists()


def test_a_map_already_read_is_never_sent_to_vision_again(maps_dir):
    gc = _GC()
    MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    out = MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    assert gc.asked == 1 and out["already"] == 1


def test_a_not_relevant_answer_is_recorded_and_not_asked_again(maps_dir):
    gc = _GC({"is_relevant": False})
    MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    rec = _record(maps_dir)
    assert (
        rec["depicted"] is None
        and rec["depicted_provenance"]["verdict"] == "not_relevant"
    )
    MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    assert gc.asked == 1


def test_no_answer_records_nothing(maps_dir, monkeypatch):
    class _Broken(_GC):
        def extract_json_with_image(self, **k):
            self.asked += 1
            raise RuntimeError("down")

    gc = _Broken()
    out = MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    assert out["read"] == 0 and "depicted_provenance" not in _record(maps_dir)
    MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    assert gc.asked == 2  # asked again next run


def test_a_map_without_a_record_is_not_read(maps_dir, caplog):
    import logging

    gc = _GC()
    with caplog.at_level(logging.WARNING):
        out = MLS.read_in_doc_maps(
            _docs("https://www.ibiblio.org/x/maps/IX.jpg"), gc, maps_dir
        )
    assert gc.asked == 0 and out["skipped"] == 1 and "no_map_record" in caplog.text


def test_read_skips_off_host(maps_dir):
    gc = _GC()
    out = MLS.read_in_doc_maps(_docs("https://evil.example/m.jpg"), gc, maps_dir)
    assert out["skipped"] == 1 and gc.asked == 0


def test_read_loop_guard_skips_map_source_docs(maps_dir):
    gc = _GC()
    out = MLS.read_in_doc_maps(_docs(book="map-01ABC"), gc, maps_dir)
    assert out["links"] == 0 and gc.asked == 0


def test_read_failsafe_on_bad_doc(tmp_path):
    out = MLS.read_in_doc_maps([None, {"maps": "notalist"}, {}], object(), tmp_path)  # type: ignore[list-item]
    assert out["read"] == 0


def test_read_trusts_only_configured_hosts_not_the_documents_own(maps_dir):
    import json

    url = "https://archive.example/books/x/maps/m.jpg"
    (maps_dir / "index.json").write_text(json.dumps({url: "m.json"}))
    docs = _docs(url)
    docs[0]["_source_url"] = "https://archive.example/books/x/ch8.html"
    assert MLS.read_in_doc_maps(docs, _GC(), maps_dir)["read"] == 0
    assert (
        MLS.read_in_doc_maps(docs, _GC(), maps_dir, allowed_hosts=("archive.example",))[
            "read"
        ]
        == 1
    )


def test_a_blocked_record_write_is_said_and_retried(maps_dir, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    gc = _GC()
    with caplog.at_level(logging.ERROR):
        out = MLS.read_in_doc_maps(_docs(), gc, maps_dir)
    assert out["read"] == 0 and "OBSERV map_depicted.write_blocked" in caplog.text


def test_the_url_index_is_saved_after_each_document(tmp_path, monkeypatch):
    """A task stopped midway keeps the maps it already persisted (task 1.2f)."""
    from src.extraction import map_link_source as mls

    saves = []
    monkeypatch.setattr(mls, "_save_url_index", lambda p, idx: saves.append(dict(idx)))
    monkeypatch.setattr(
        mls, "_persist_one_map", lambda m, b, d, idx, *a: idx.__setitem__(m["url"], "M")
    )
    docs = [
        {"book": "B", "maps": [{"url": "u1"}]},
        {"book": "B", "maps": [{"url": "u2"}]},
    ]
    mls.persist_map_entities(docs, tmp_path)
    assert saves[0] == {"u1": "M"} and saves[-1] == {"u1": "M", "u2": "M"}
    assert len(saves) == 3  # one per document + the final
