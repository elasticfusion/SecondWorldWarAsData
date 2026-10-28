"""M3: caption-driven image -> PersonID linkage."""

import json
from pathlib import Path

from src.extraction.caption_names import parse_caption_person_names as P
import src.extraction.image_person_link as IPL


# --- caption parser ---
def test_caption_left_to_right():
    out = P(
        "ALLIED INVASION CHIEFS. Left to right: General Bradley, Admiral Ramsay, "
        "Air Chief Marshal Tedder, General Eisenhower. This photograph was taken in 1944."
    )
    assert out == ["Bradley", "Ramsay", "Tedder", "Eisenhower"]


def test_caption_positional_german():
    out = P(
        "GERMAN FIELD COMMANDERS. Upper left, General von Schlieben; upper right, "
        "General Marcks; lower left, General Geyr von Schweppenburg; lower right, General Dollmann."
    )
    assert out == ["von Schlieben", "Marcks", "Geyr von Schweppenburg", "Dollmann"]


def test_caption_single_subject():
    assert P("RANGERS led by Lt. Col. James E. Rudder.") == ["James E. Rudder"]


def test_caption_prose_and_terrain_empty():
    assert P("PREINVASION SCENES. Soldiers in mess line in a marshaling camp.") == []
    assert (
        P("HEDGEROW COUNTRY. Aerial view of typical hedgerow terrain in Normandy.")
        == []
    )


# --- resolver ---
def _people(tmp_path, names):
    import ulid

    d = tmp_path / "people"
    d.mkdir()
    for i, n in enumerate(names):
        # Valid ULID PersonID (the people schema requires it; a fake id -> guarded write BLOCKED,
        # which would make the write-path assertions silently fail — real-write-path discipline).
        (d / f"p{i}.json").write_text(
            json.dumps({"PersonID": str(ulid.new()), "name": n})
        )
    return d


def test_resolve_full_and_surname(tmp_path):
    d = _people(tmp_path, ["Omar Bradley", "Dwight D. Eisenhower"])
    full, surname = IPL._build_people_index(d)
    assert (
        IPL._resolve_person_file("Bradley", full, surname).name == "p0.json"
    )  # surname
    assert (
        IPL._resolve_person_file("Dwight D. Eisenhower", full, surname).name
        == "p1.json"
    )  # full
    assert IPL._resolve_person_file("Patton", full, surname) is None  # unknown


def test_resolve_ambiguous_surname_dropped(tmp_path):
    d = _people(tmp_path, ["Omar Bradley", "John Bradley"])
    full, surname = IPL._build_people_index(d)
    assert (
        IPL._resolve_person_file("Bradley", full, surname) is None
    )  # ambiguous -> no link


# --- end-to-end link ---
def test_link_appends_document_image(tmp_path, monkeypatch):
    d = _people(tmp_path, ["Omar Bradley", "Bertram Ramsay"])
    docs = [
        {
            "book": "XChannel",
            "images": [
                {
                    "url": "https://ibiblio.org/x/icn/37.jpg",
                    "caption": "ALLIED INVASION CHIEFS. Left to right: General Bradley, Admiral Ramsay.",
                }
            ],
        }
    ]
    # vision gate off (no client) -> caption-driven
    summary = IPL.link_images_to_people(docs, d, grok_client=None)
    assert summary["linked"] == 2
    b = json.loads((d / "p0.json").read_text())
    assert b["images"][0]["url"].endswith("icn/37.jpg")
    assert "ALLIED INVASION CHIEFS" in b["images"][0]["title"]
    assert b["images"][0]["source"] == "document:XChannel"


def test_link_dedup_by_url(tmp_path):
    d = _people(tmp_path, ["Omar Bradley"])
    docs = [
        {
            "book": "X",
            "images": [
                {"url": "https://x/a.jpg", "caption": "Left to right: General Bradley."}
            ],
        }
    ]
    IPL.link_images_to_people(docs, d, grok_client=None)
    IPL.link_images_to_people(
        docs, d, grok_client=None
    )  # second run must not duplicate
    rec = json.loads((d / "p0.json").read_text())
    assert len([i for i in rec["images"] if i["url"] == "https://x/a.jpg"]) == 1


def test_link_skips_non_people_photo_when_vision_says_map(tmp_path):
    d = _people(tmp_path, ["Omar Bradley"])
    docs = [
        {
            "book": "X",
            "images": [
                {"url": "https://x/m.jpg", "caption": "Left to right: General Bradley."}
            ],
        }
    ]

    class _VisionMap:
        pass

    import src.extraction.image_person_link as M

    # patch _is_people_photo to simulate a vision 'map' classification
    orig = M._is_people_photo
    M._is_people_photo = lambda caption, url, gc: False
    try:
        summary = M.link_images_to_people(docs, d, grok_client=object())
    finally:
        M._is_people_photo = orig
    assert summary["linked"] == 0 and summary["skipped"] >= 1


def test_link_failsafe_on_bad_docs(tmp_path):
    d = _people(tmp_path, ["Omar Bradley"])
    out = IPL.link_images_to_people(
        [None, {"images": "notalist"}, {}], d, grok_client=None
    )
    assert out["linked"] == 0


def test_observability_remediation(tmp_path, caplog):
    """M3 observability MUST-FIX: unresolved names, no-names, and non-people skips emit OBSERV."""
    import logging

    d = _people(tmp_path, ["Omar Bradley"])
    docs = [
        {
            "book": "X",
            "images": [
                {
                    "url": "https://x/a.jpg",
                    "caption": "Left to right: General Patton.",
                },  # unresolved
                {
                    "url": "https://x/b.jpg",
                    "caption": "A quiet field at dawn.",
                },  # no names
            ],
        },
    ]
    with caplog.at_level(logging.INFO, logger="src.extraction.image_person_link"):
        IPL.link_images_to_people(docs, d, grok_client=None)
    assert "OBSERV image_person.unresolved name=Patton" in caplog.text
    assert "OBSERV image_person.no_names" in caplog.text


def test_observability_remediation_vision_paths(tmp_path, caplog):
    """M3 observability: not_people_photo skip + vision_error fail-safe emit OBSERV."""
    import logging

    d = _people(tmp_path, ["Omar Bradley"])
    docs = [
        {
            "book": "X",
            "images": [
                {
                    "url": "https://x/m.jpg",
                    "caption": "Left to right: General Bradley.",
                },
            ],
        }
    ]

    # vision says map -> not_people_photo skip
    import src.extraction.image_person_link as M

    orig = M._is_people_photo
    M._is_people_photo = lambda caption, url, gc: False
    try:
        with caplog.at_level(logging.INFO, logger="src.extraction.image_person_link"):
            M.link_images_to_people(docs, d, grok_client=object())
    finally:
        M._is_people_photo = orig
    assert "OBSERV image_person.skipped reason=not_people_photo" in caplog.text

    # vision client raises inside _is_people_photo -> vision_error fail-safe=people
    caplog.clear()

    class BoomCaptioner:
        def __init__(self, grok_client=None):
            pass

        def caption(self, *a, **k):
            raise RuntimeError("vision boom")

    import src.extraction.image_captioner as IC_mod

    orig_cap = IC_mod.ImageCaptioner
    IC_mod.ImageCaptioner = BoomCaptioner
    try:
        with caplog.at_level(
            logging.WARNING, logger="src.extraction.image_person_link"
        ):
            M.link_images_to_people(docs, d, grok_client=object())
    finally:
        IC_mod.ImageCaptioner = orig_cap
    assert "OBSERV image_person.vision_error" in caplog.text


def test_ulid_attribution_backfill(tmp_path):
    """CRITICAL backfill: the linked people image carries the SourceSectionID ULID of its source."""
    import ulid as _ulid

    d = _people(tmp_path, ["Omar Bradley"])
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
                {"url": "https://x/b.jpg", "caption": "Left to right: General Bradley."}
            ],
        }
    ]
    IPL.link_images_to_people(docs, d, grok_client=None)
    rec = json.loads((d / "p0.json").read_text())
    assert rec["images"][0]["SourceSectionID"] == sid
