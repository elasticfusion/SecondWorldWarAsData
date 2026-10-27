"""M1: follow in-document map links -> map-as-source."""

import json
from pathlib import Path

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
    assert (
        MLS._host_allowed("https://cdn.example/m.jpg", source_host="cdn.example")
        is True
    )


# --- ingest: fetch -> vision -> recycle ---
def test_ingest_recycles_relevant_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MLS, "_ssrf_safe_image_url", lambda u: u)  # pass SSRF gate
    # vision approves + returns depicted
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr(SEM, "_build_verification_prompt", lambda *a, **k: "PROMPT")

    class _GC:
        def extract_json_with_image(self, **k):
            return {
                "is_relevant": True,
                "is_government_map": True,
                "depicted": {
                    "units": ["101st Airborne"],
                    "places": ["Ste. Mère-Église"],
                    "description": "Airborne drop zones map.",
                },
            }

    monkeypatch.setattr(MLS, "_content_is_new", lambda url: True)
    docs = [
        {
            "book": "XChannel",
            "chapter_title": "The Sixth of June",
            "maps": [
                {
                    "url": "https://www.ibiblio.org/x/maps/VIII.jpg",
                    "map_id": "VIII",
                    "description": "Map VIII",
                }
            ],
        }
    ]
    out = MLS.ingest_in_doc_map_links(docs, _GC())
    assert out["recycled"] == 1 and out["verified"] == 1
    base = tmp_path / "contentrepository" / "map-VIII"
    assert (base / "map-VIII-meta.yaml").is_file()
    assert "101st Airborne" in (base / "map-VIIIa-content.md").read_text()


def test_ingest_skips_off_host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MLS, "_ssrf_safe_image_url", lambda u: u)
    docs = [
        {
            "book": "X",
            "maps": [
                {
                    "url": "https://evil.example/m.jpg",
                    "map_id": "Z",
                    "description": "Map Z",
                }
            ],
        }
    ]
    out = MLS.ingest_in_doc_map_links(docs, object())
    assert out["recycled"] == 0 and out["skipped"] == 1


def test_ingest_loop_guard_skips_map_source_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(
        MLS,
        "_ingest_one_link",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    # a map-derived doc (book=map-*) must be skipped entirely
    docs = [
        {
            "book": "map-01ABC",
            "maps": [{"url": "https://ibiblio.org/m.jpg", "map_id": "A"}],
        }
    ]
    out = MLS.ingest_in_doc_map_links(docs, object())
    assert out["links"] == 0 and called["n"] == 0


def test_ingest_deduped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MLS, "_ssrf_safe_image_url", lambda u: u)
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr(SEM, "_build_verification_prompt", lambda *a, **k: "P")

    class _GC:
        def extract_json_with_image(self, **k):
            return {"is_relevant": True, "depicted": {"description": "x"}}

    monkeypatch.setattr(MLS, "_content_is_new", lambda url: False)  # already seen
    docs = [
        {
            "book": "X",
            "maps": [
                {
                    "url": "https://ibiblio.org/m.jpg",
                    "map_id": "A",
                    "description": "Map A",
                }
            ],
        }
    ]
    out = MLS.ingest_in_doc_map_links(docs, _GC())
    assert out["deduped"] == 1 and out["recycled"] == 0


def test_ingest_failsafe_on_bad_doc():
    # non-dict docs / missing fields must not raise
    out = MLS.ingest_in_doc_map_links([None, {"maps": "notalist"}, {}], object())  # type: ignore[list-item]
    assert out["recycled"] == 0


def test_ingest_allows_same_host_via_source_url(tmp_path, monkeypatch):
    """A map link on the SAME host as the source doc (_source_url) is allowed even if the host is
    not on the static ibiblio allowlist."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MLS, "_ssrf_safe_image_url", lambda u: u)
    import src.extraction.search_external_maps as SEM

    monkeypatch.setattr(SEM, "_build_verification_prompt", lambda *a, **k: "P")
    monkeypatch.setattr(MLS, "_content_is_new", lambda url: True)

    class _GC:
        def extract_json_with_image(self, **k):
            return {"is_relevant": True, "depicted": {"description": "map"}}

    docs = [
        {
            "book": "SomeBook",
            "_source_url": "https://archive.example/books/x/ch8.html",
            "maps": [
                {
                    "url": "https://archive.example/books/x/maps/m.jpg",
                    "map_id": "M",
                    "description": "Map M",
                }
            ],
        }
    ]
    out = MLS.ingest_in_doc_map_links(docs, _GC())
    assert out["recycled"] == 1  # same-host allowed via _source_url
