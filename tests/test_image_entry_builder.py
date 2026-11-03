"""DRY P2.2 guard: the byte-identical image-entry builder shared by the image->person and
image->place linkers is now _entity_common.build_image_entry. Pin its contract + both delegate.
"""

from src.extraction._entity_common import build_image_entry


def test_build_image_entry_contract():
    e = build_image_entry("http://x/i.jpg", "A caption", "Breakout", "01SS")
    assert e == {
        "url": "http://x/i.jpg",
        "title": "A caption",
        "source": "document:Breakout",
        "SourceSectionID": "01SS",
    }


def test_build_image_entry_no_book_no_section():
    e = build_image_entry("http://x/i.jpg", "c", "", None)
    assert e == {"url": "http://x/i.jpg", "title": "c", "source": "document"}
    assert "SourceSectionID" not in e


def test_caption_capped_300():
    e = build_image_entry("u", "x" * 500, "B", None)
    assert len(e["title"]) == 300


def test_both_linkers_delegate_and_agree():
    from src.extraction.image_person_link import _build_person_image_entry
    from src.extraction.image_place_link import _build_place_image_entry

    args = ("http://x/i.jpg", "Omaha Beach", "Cross-Channel", "01ABC")
    assert _build_person_image_entry(*args) == build_image_entry(*args)
    assert _build_place_image_entry(*args) == build_image_entry(*args)
    assert _build_person_image_entry(*args) == _build_place_image_entry(*args)
