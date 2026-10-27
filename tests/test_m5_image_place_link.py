"""M5: terrain/aerial image -> PlaceID linkage (caption-first, Grok fallback, create-when-new)."""

import json
import pathlib
import tempfile

import ulid

import src.extraction.image_place_link as IPL


# --- classifier ---
def test_classify_caption_terrain():
    assert IPL.classify_terrain_aerial("AERIAL VIEW OF OMAHA BEACH")[0] is True
    assert IPL.classify_terrain_aerial("HEDGEROW COUNTRY. Typical terrain.")[0] is True
    assert (
        IPL.classify_terrain_aerial("ALLIED INVASION CHIEFS. General Bradley.")[0]
        is False
    )


def test_classify_grok_fallback_describes_subject():
    class Grok:
        def chat_completion(self, p):
            return "TERRAIN"

    # ambiguous caption (no keyword) -> Grok decides
    is_t, method = IPL.classify_terrain_aerial(
        "Scene near the coast.", grok_client=Grok()
    )
    assert is_t and method == "grok"

    class GrokOther:
        def chat_completion(self, p):
            return "OTHER"

    assert (
        IPL.classify_terrain_aerial("Scene near the coast.", grok_client=GrokOther())[0]
        is False
    )


def test_classify_grok_error_failsafe():
    class Boom:
        def chat_completion(self, p):
            raise RuntimeError("x")

    assert IPL.classify_terrain_aerial("Scene.", grok_client=Boom()) == (
        False,
        "grok-error",
    )


# --- place-name parser ---
def test_parse_place_names():
    assert IPL.parse_caption_place_names(
        "AERIAL VIEW OF UTAH/OMAHA BEACH, 6 June 1944."
    ) == [
        "UTAH BEACH",
        "OMAHA BEACH",
    ]
    assert IPL.parse_caption_place_names("TERRAIN ON OMAHA BEACH looking inland.") == [
        "OMAHA BEACH"
    ]
    assert IPL.parse_caption_place_names("AERIAL VIEW OF THE PORT OF CHERBOURG.") == [
        "CHERBOURG"
    ]
    assert IPL.parse_caption_place_names("HEDGEROW COUNTRY.") == []


# --- end-to-end (real guarded write, valid ULID PlaceIDs) ---
def _places(tmp_path, names):
    d = tmp_path / "places"
    d.mkdir()
    index = {}
    for i, n in enumerate(names):
        pid = str(ulid.new())
        fn = f"p{i}.json"
        (d / fn).write_text(
            json.dumps({"PlaceID": pid, "current_name": n, "aliases": []})
        )
        index[n.lower()] = fn
    (d / "index.json").write_text(json.dumps(index))
    return d


def test_link_appends_to_existing_place(tmp_path):
    d = _places(tmp_path, ["Omaha Beach"])
    docs = [
        {
            "book": "XChannel",
            "images": [
                {
                    "url": "https://ibiblio.org/x/terrain.jpg",
                    "caption": "TERRAIN ON OMAHA BEACH.",
                }
            ],
        }
    ]
    s = IPL.link_images_to_places(docs, d, grok_client=None)
    assert s["terrain"] == 1 and s["linked"] == 1 and s["created"] == 0
    rec = json.loads((d / "p0.json").read_text())
    assert rec["images"][0]["url"].endswith("terrain.jpg")
    assert rec["images"][0]["source"] == "document:XChannel"


def test_link_creates_new_place_when_described(tmp_path):
    # steer: a described-but-unindexed place is routed to the place pipeline (created), then linked
    d = _places(tmp_path, ["Omaha Beach"])
    docs = [
        {
            "book": "X",
            "images": [
                {"url": "https://x/a.jpg", "caption": "AERIAL VIEW OF CARENTAN."}
            ],
        }
    ]
    s = IPL.link_images_to_places(docs, d, grok_client=None)
    assert s["created"] == 1 and s["linked"] == 1
    # a new place file now exists for Carentan with the image linked
    hits = []
    for pf in d.glob("*.json"):
        if pf.name == "index.json":
            continue
        rec = json.loads(pf.read_text())
        if rec.get("current_name", "").lower() == "carentan":
            hits.append(rec)
    assert len(hits) == 1 and hits[0]["images"][0]["url"] == "https://x/a.jpg"


def test_link_dedup_by_url(tmp_path):
    d = _places(tmp_path, ["Omaha Beach"])
    docs = [
        {
            "book": "X",
            "images": [
                {"url": "https://x/a.jpg", "caption": "TERRAIN ON OMAHA BEACH."}
            ],
        }
    ]
    IPL.link_images_to_places(docs, d, grok_client=None)
    IPL.link_images_to_places(docs, d, grok_client=None)
    rec = json.loads((d / "p0.json").read_text())
    assert len([i for i in rec["images"] if i["url"] == "https://x/a.jpg"]) == 1


def test_link_skips_non_terrain(tmp_path):
    d = _places(tmp_path, ["Omaha Beach"])
    docs = [
        {
            "book": "X",
            "images": [{"url": "https://x/p.jpg", "caption": "General Bradley at HQ."}],
        }
    ]
    s = IPL.link_images_to_places(docs, d, grok_client=None)
    assert s["linked"] == 0 and s["terrain"] == 0


def test_link_failsafe_bad_docs(tmp_path):
    d = _places(tmp_path, ["Omaha Beach"])
    s = IPL.link_images_to_places([None, {"images": "x"}, {}], d, grok_client=None)
    assert s["linked"] == 0


def test_ulid_attribution_from_source_section(tmp_path):
    """CRITICAL: the linked image carries the SourceSectionID ULID of its source_section."""
    import ulid as _ulid

    d = _places(tmp_path, ["Omaha Beach"])
    # a committed source_section for book 'XChannel' with a ULID
    sid = str(_ulid.new())
    sec = tmp_path / "source_section"
    sec.mkdir()
    (sec / f"{sid}.json").write_text(
        json.dumps({"SourceSectionID": sid, "source": {"book": "XChannel"}})
    )
    docs = [
        {
            "book": "XChannel",
            "images": [
                {"url": "https://x/t.jpg", "caption": "TERRAIN ON OMAHA BEACH."}
            ],
        }
    ]
    s = IPL.link_images_to_places(docs, d, grok_client=None)
    assert s["linked"] == 1
    rec = json.loads((d / "p0.json").read_text())
    assert (
        rec["images"][0]["SourceSectionID"] == sid
    )  # durable ULID attribution written
    assert rec["images"][0]["source"] == "document:XChannel"
