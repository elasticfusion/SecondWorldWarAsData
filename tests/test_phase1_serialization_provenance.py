"""PR3: _doc_to_dict must serialize all populated provenance fields —
page_markers (M1) and Image.paragraph_number (M2) were silently dropped."""

from __future__ import annotations

from src.models import MarkdownDocument, Paragraph, Image, PageMarker
import phase1_parse as p1


def _doc():
    return MarkdownDocument(
        book="St. Vith",
        chapter_number=1,
        chapter_title="T",
        section_id="",
        author="A",
        series="S",
        license="Public Domain",
        paragraphs=[
            Paragraph(absolute_number=0, text="body", section_id="", source_file="c.md")
        ],
        images=[
            Image(type="embedded", resource_id="r1", alt_text="fig", paragraph_number=7)
        ],
        page_markers=[
            PageMarker(page_number=42, paragraph_number=3, marker_type="anchor")
        ],
    )


def test_doc_to_dict_serializes_page_markers():
    """M1: page_markers (anchor/separator -> paragraph provenance) is serialized."""
    d = p1._doc_to_dict(_doc())
    assert "page_markers" in d, "page_markers dropped from serialization"
    assert d["page_markers"] == [
        {"page_number": 42, "paragraph_number": 3, "marker_type": "anchor"}
    ]


def test_doc_to_dict_serializes_image_paragraph_number():
    """M2: Image.paragraph_number (figure->paragraph provenance) is serialized."""
    d = p1._doc_to_dict(_doc())
    assert (
        d["images"][0].get("paragraph_number") == 7
    ), "image paragraph_number dropped from serialization"
