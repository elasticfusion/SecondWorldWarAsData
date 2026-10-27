"""M2: capture caption-only / bare embedded images (relative src + caption pairing)."""

from src.models import MarkdownDocument
from src.parser import (
    extract_images,
    _add_images_to_doc,
    _is_caption_line,
    _pair_image_captions,
)

_BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"


def _doc():
    return MarkdownDocument(
        book="X",
        chapter_number=8,
        chapter_title="t",
        section_id="s",
        author="",
        series="",
        license="",
    )


# --- part 1: relative external image resolution ---
def test_relative_image_resolved_against_base():
    out = extract_images("![](icn/USA-E-XChannel-37.jpg)", base_url=_BASE)
    assert out == [
        ("external", _BASE.rsplit("/", 1)[0] + "/icn/USA-E-XChannel-37.jpg", "", "")
    ]


def test_absolute_unchanged_and_no_base_dropped_and_nonimage_ignored():
    assert extract_images("![a](https://x/y.jpg)", base_url=_BASE) == [
        ("external", "https://x/y.jpg", "a", "")
    ]
    assert extract_images("![](icn/x.jpg)") == []  # no base -> not absolutized
    assert (
        extract_images("![x](notes/foo.html)", base_url=_BASE) == []
    )  # non-image relative ignored
    # Joplin resource (:/...) not mis-captured by the relative branch
    assert all(
        "notes/foo" not in u
        for _, u, _, _ in extract_images("![x](notes/foo.html)", base_url=_BASE)
    )


# --- part 2: caption pairing ---
def test_caption_line_heuristic():
    assert (
        _is_caption_line("ALLIED INVASION CHIEFS. Left to right: Bradley, Ramsay.")
        is True
    )
    assert _is_caption_line("PREINVASION SCENES. Soldiers in mess line.") is True
    assert _is_caption_line("On 8 May General Eisenhower set D Day.") is False
    assert _is_caption_line("MAP") is False  # one word / too short


def test_following_caption_paired_into_image():
    content = (
        "![](icn/USA-E-XChannel-37.jpg)\n\n"
        "ALLIED INVASION CHIEFS. Left to right: Bradley, Ramsay, Tedder, Eisenhower.\n\n"
        "Ordinary prose that follows and must NOT be a caption."
    )
    doc = _doc()
    _add_images_to_doc(doc, content, base_url=_BASE)
    assert len(doc.images) == 1
    assert doc.images[0].url.endswith("icn/USA-E-XChannel-37.jpg")
    assert doc.images[0].caption and "Bradley" in doc.images[0].caption


def test_no_caption_stays_none():
    content = "![](icn/USA-E-XChannel-99.jpg)\n\njust ordinary prose, not a caption at all here."
    doc = _doc()
    _add_images_to_doc(doc, content, base_url=_BASE)
    assert doc.images[0].caption is None


def test_pairing_failsafe_on_bad_doc():
    # _pair_image_captions must not raise on a doc with no images / odd content
    doc = _doc()
    _pair_image_captions(doc, "no images here")  # no raise


def test_serialization_carries_url_and_caption():
    import phase1_parse

    content = (
        "![](icn/USA-E-XChannel-37.jpg)\n\n"
        "GERMAN FIELD COMMANDERS. Upper left, General von Schlieben."
    )
    doc = _doc()
    _add_images_to_doc(doc, content, base_url=_BASE)
    d = phase1_parse._doc_to_dict(doc)
    img = d["images"][0]
    assert img["url"].endswith("icn/USA-E-XChannel-37.jpg")
    assert img["caption"] and "Schlieben" in img["caption"]
