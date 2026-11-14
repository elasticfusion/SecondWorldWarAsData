"""Image captions and full-size capture for thumbnail-linked figures.

XChannel-8 E2E: every figure is a thumbnail linked to the large image
(``<a href="img/X.jpg"><img src="icn/X.jpg"></a>``). The LARGE image is the one captured; its
printed caption ("ALLIED INVASION CHIEFS. Left to right: ...") was paired for 0 of 39 images
(pairing matched only markdown ``![`` lines and bare basenames) and Phase 2 never wrote it into
the image record or the vision prompt.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from src import parser as P
from src.models import MarkdownDocument, Metadata

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"
FIXTURE = Path(__file__).parent / "fixtures" / "notes" / "USA-E-XChannel-8.md.gz"


def _fig(n: str) -> str:
    return (
        f'> <a href="img/X-{n}.jpg" target="mapwindow"><img src="icn/X-{n}.jpg" /></a>'
    )


def _captions(content: str) -> dict:
    doc = MarkdownDocument(
        book="B",
        chapter_number=8,
        chapter_title="t",
        section_id="s",
        author="",
        series="",
        license="",
    )
    P._add_images_to_doc(doc, content, base_url=BASE)
    return {i.url.rsplit("/", 1)[-1]: i.caption for i in doc.images}


# --- full-size capture ----------------------------------------------------------------------------


def test_thumbnail_link_captures_large_image_only():
    caps = _captions(_fig("35"))
    assert list(caps) == ["X-35.jpg"]
    doc_urls = [u for _t, _r, _a, u in P.extract_images(_fig("35"), base_url=BASE)]
    doc_urls += [r for _t, r, _a, _u in P.extract_images(_fig("35"), base_url=BASE)]
    assert any(u.endswith("/img/X-35.jpg") for u in doc_urls)
    assert not any(
        "/icn/" in (u or "") for u in doc_urls
    )  # the thumbnail is not an image


# --- caption pairing ------------------------------------------------------------------------------


def test_html_figure_caption_paired_and_emphasis_stripped():
    md = (
        f"{_fig('37')}  \n> ALLIED INVASION CHIEFS. *Left to right: General Bradley.*\n"
    )
    assert _captions(md) == {
        "X-37.jpg": "ALLIED INVASION CHIEFS. Left to right: General Bradley."
    }


def test_caption_between_rows_covers_the_whole_group():
    # "(above) ... (below)": the caption sits between the two photos of the group
    md = (
        f"{_fig('35')}\n>\n> PREINVASION SCENES. *Mess line (above), loading LSTs (below).*\n"
        f">\n{_fig('36')}\n\n*--271--*\n"
    )
    caps = _captions(md)
    assert caps["X-35.jpg"] == caps["X-36.jpg"] and caps["X-35.jpg"].startswith(
        "PREINVASION SCENES."
    )


def test_caption_after_the_group_covers_every_image_in_it():
    md = (
        f"{_fig('53')}\n>\n{_fig('54')}\n>\n{_fig('55')}\n>\n"
        "> TERRAIN ON OMAHA BEACH. *Bluffs (top), draw (center), beach (bottom).*\n"
    )
    assert len(set(_captions(md).values())) == 1


def test_italic_continuation_without_period_is_a_caption():
    assert P._is_caption_line("PARACHUTE TROOPS *marching onto airfield on 5 June.*")
    assert not P._is_caption_line("On 8 May *General Eisenhower* set D Day.")


def test_narrative_or_page_marker_stops_the_group():
    md = f"{_fig('44')}\n\n*--285--*\n\nTHE NEXT FIGURE. caption of a different page.\n"
    assert _captions(md) == {"X-44.jpg": None}
    md2 = (
        "> ALLIED INVASION CHIEFS. Left to right.\n>\n> Ordinary narrative sentence here.\n"
        f">\n{_fig('40')}\n"
    )
    assert _captions(md2) == {
        "X-40.jpg": None
    }  # a caption beyond narrative is not its own


def test_basename_collision_is_not_paired():
    # X-3.jpg must not pick up the caption of X-35.jpg / a different directory's X-3.jpg
    md = (
        '> <a href="other/X-3.jpg"><img src="icn/X-3.jpg" /></a>\n> OTHER CAPTION HERE. x\n\n'
        "Narrative between the figures, long enough.\n\n"
        f"{_fig('3')}\n\nNarrative after, not a caption at all.\n"
    )
    caps = _captions(md)
    assert caps["X-3.jpg"] is None


def test_headings_and_page_markers_are_never_captions():
    assert not P._is_caption_line("## CHAPTER VIII THE SIXTH OF JUNE")
    assert not P._is_caption_line("# THE SIXTH OF JUNE")
    md = f"## CHAPTER VIII THE SIXTH\n\n{_fig('1')}\n\n### THE INVASION IS LAUNCHED\n"
    assert _captions(md) == {"X-1.jpg": None}


def test_captionless_neighbour_does_not_inherit_a_plain_caption():
    # Case A: [figA][capA][figB] - capA names no positions, so it is figA's alone
    md_a = (
        f"{_fig('1')}\n> ALLIED INVASION CHIEFS. Left, Bradley.\n>\n{_fig('2')}\n\n"
        "Ordinary narrative paragraph follows here.\n"
    )
    assert _captions(md_a) == {
        "X-1.jpg": "ALLIED INVASION CHIEFS. Left, Bradley.",
        "X-2.jpg": None,
    }
    # Case B: [figX][figB][capB] - figX does not take figB's caption across it
    md_b = f"{_fig('1')}\n>\n{_fig('2')}\n> AERIAL VIEW OF UTAH BEACH.\n"
    assert _captions(md_b) == {"X-1.jpg": None, "X-2.jpg": "AERIAL VIEW OF UTAH BEACH."}


def test_in_text_reference_before_the_figure_is_skipped():
    # An earlier in-text "Map 2" link is not the figure: pair from the line DISPLAYING it.
    md = (
        '> The counterattack (<a href="img/X-2.jpg" target="mapwindow">Map 2</a>) failed.\n'
        "> More narrative here, long enough to be a paragraph.\n\n"
        f"{_fig('2')}  \n> GERMAN COUNTERATTACK IN THE COTENTIN. *Captured map.*\n"
    )
    assert _captions(md) == {
        "X-2.jpg": "GERMAN COUNTERATTACK IN THE COTENTIN. Captured map."
    }


def test_single_photo_caption_is_not_inherited_by_the_next_photo():
    # "Left to right" names people in ONE photo; "(top)" names the image ABOVE the caption.
    for cap in ("PHOTO A. Left to right: X, Y.", "PHOTO A. Bluffs (top)."):
        md = f"{_fig('1')}\n> {cap}\n>\n{_fig('2')}\n\nOrdinary narrative paragraph here.\n"
        assert _captions(md)["X-2.jpg"] is None, cap


def test_position_words_that_are_not_positions_do_not_share_a_caption():
    md = f"{_fig('1')}\n> FIGHTING IN NORMANDY. *Village (Lower Normandy).*\n>\n{_fig('2')}\n"
    assert _captions(md)["X-2.jpg"] is None
    md2 = f"{_fig('1')}\n>\n{_fig('2')}\n> TOP SECRET MAP. *Plan (top secret).*\n"
    assert _captions(md2)["X-1.jpg"] is None


def test_in_text_link_does_not_borrow_the_next_figures_caption():
    md = (
        '> See <a href="img/X-1.jpg">the chart</a> here.\n> <img src="icn/X-2.jpg" />\n'
        ">\n> GALLERY OF FIGURES. foo\n"
    )
    assert P._find_following_caption(md.splitlines(), ["img/X-1.jpg"]) is None


@pytest.mark.parametrize(
    "caption,title",
    [
        ("OMAHA BEACH. A wrecked tank on the sand.", "OMAHA BEACH"),
        ("PT BOATS. I saw them.", "PT BOATS"),
        (
            "AERIAL VIEW OF OMAHA BEACH ON D PLUS 1. Note the draws.",
            "AERIAL VIEW OF OMAHA BEACH ON D PLUS 1",
        ),
    ],
)
def test_title_stops_at_the_headline(caption, title):
    from src.extraction.images import _caption_title

    assert _caption_title(caption) == title


def test_multiline_anchor_figure_is_captioned():
    md = (
        '> <a href="img/X-7.jpg" target="mapwindow">\n> <img src="icn/X-7.jpg" /></a>\n'
        "> AERIAL VIEW OF UTAH BEACH.\n"
    )
    assert _captions(md) == {"X-7.jpg": "AERIAL VIEW OF UTAH BEACH."}


def test_tag_only_anchor_line_between_figure_and_caption_is_skipped():
    md = f'{_fig("8")}\n\n<span id="page279"></span>\n\n> PARACHUTE TROOPS *on the field.*\n'
    assert _captions(md) == {"X-8.jpg": "PARACHUTE TROOPS on the field."}


@pytest.mark.parametrize(
    "line,ok",
    [
        ("U.S. TROOPS LAND. On the beach.", True),
        ("GEN. EISENHOWER AND STAFF. At Portsmouth.", True),
        ("UTAH BEACH was quiet that morning.", False),
        # known limit: a 3-letter final headline word reads as an abbreviation ("GEN.")
        ("THE END OF WAR. Left to right: a b c", False),
    ],
)
def test_abbreviation_led_captions(line, ok):
    assert P._is_caption_line(line) is ok


def test_title_is_capped_for_an_all_caps_caption():
    from src.extraction.images import _caption_title

    long_caption = " ".join(["WORD"] * 60)
    title = _caption_title(long_caption)
    assert title and len(title) <= 100 and title.endswith("WORD")


def test_pairing_failure_is_actionable(monkeypatch, caplog):
    import logging

    def boom(*_a, **_k):
        raise RuntimeError("bad")

    monkeypatch.setattr(P, "_find_following_caption", boom)
    with caplog.at_level(logging.WARNING):
        _captions(_fig("1"))
    assert any("caption_pairing_failed" in r.getMessage() for r in caplog.records)


def test_real_chapter_every_photograph_captioned(tmp_path):
    src = tmp_path / "USA-E-XChannel-8.md"
    src.write_bytes(gzip.decompress(FIXTURE.read_bytes()))
    meta = Metadata(
        book="USA-E-XChannel",
        chapter_title="The Sixth of June",
        author="",
        series="",
        license="",
        source_url=BASE,
    )
    doc = P.parse_content_file(
        src, section_id="a", start_paragraph_num=1, metadata=meta
    )
    photos = [i for i in doc.images if "/img/USA-E-XChannel-" in i.url]
    assert len(photos) == 26 and all(i.caption for i in photos)
    map2 = next(i for i in doc.images if i.url.endswith("/maps/USA-E-XChannel-2.jpg"))
    assert map2.caption.startswith("GERMAN COUNTERATTACK IN THE COTENTIN.")
    assert not any("/icn/" in i.url or i.url.endswith("m.jpg") for i in doc.images)
    assert all("*" not in i.caption for i in photos)


# --- Phase 2 image record + vision prompt ---------------------------------------------------------


@pytest.mark.parametrize(
    "img,title",
    [
        (
            {
                "url": "https://h/img/a.jpg",
                "alt_text": "",
                "caption": "HEDGEROW COUNTRY. Aerial.",
            },
            "HEDGEROW COUNTRY",
        ),
        (
            {"url": "https://h/img/a.jpg", "alt_text": "Alt", "caption": "CAPS. x"},
            "Alt",
        ),
        ({"url": "https://h/img/a.jpg", "alt_text": ""}, "Untitled"),
        (
            {
                "url": "https://h/img/a.jpg",
                "alt_text": "",
                "caption": "U.S. TROOPS LAND. On",
            },
            "U.S. TROOPS LAND",
        ),
        (
            {
                "url": "https://h/img/a.jpg",
                "alt_text": "",
                "caption": "GEN. EISENHOWER. x",
            },
            "GEN. EISENHOWER",
        ),
        (
            {
                "url": "https://h/img/a.jpg",
                "alt_text": "",
                "caption": "AERIAL VIEW OF D PLUS 1.",
            },
            "AERIAL VIEW OF D PLUS 1",
        ),
    ],
)
def test_image_record_carries_printed_caption(img, title):
    from src.extraction.images import _create_image_record

    rec = _create_image_record(
        img, "01M6J3NGH7SB27Q1Q9QAKG9H7N", None, None, None, None, None, None, "B"
    )
    assert rec["caption"] == (img.get("caption") or None)
    assert rec["image_title"] == title


def test_image_record_validates_against_schema():
    import jsonschema

    from src.extraction.images import _create_image_record
    from src.schemas.images_output import IMAGES_OUTPUT_SCHEMA

    rec = _create_image_record(
        {"url": "https://h/img/a.jpg", "alt_text": "", "caption": "CAPS HEAD. x"},
        "01M6J3NGH7SB27Q1Q9QAKG9H7N",
        None,
        None,
        None,
        None,
        None,
        None,
        "B",
    )
    jsonschema.validate(rec, IMAGES_OUTPUT_SCHEMA)


def test_vision_prompt_includes_printed_caption():
    from src.extraction.image_captioner import _prompt

    p = _prompt(
        {"caption": "ALLIED INVASION CHIEFS. Left to right: Bradley.", "book": "B"}
    )
    assert "Printed caption" in p and "Bradley" in p
    assert "Printed caption" not in _prompt({"book": "B"})
