"""Step 1: parser extract_maps captures raw-HTML <a href> maps (hi-res) + _add_maps_to_doc builds
the per-map reference footprint (paragraph_numbers MANDATORY, OI3).
"""

from src.parser import extract_maps, _map_reference_footprint, _iter_html_anchors
from src.models import Paragraph

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/"


def test_extract_maps_captures_anchor_maps_hires():
    md = (
        '<a href="maps/USA-E-XChannel-1.jpg"><img src="maps/USA-E-XChannel-1m.jpg"></a> '
        '<a href="maps/USA-E-XChannel-XII.jpg">Map XII</a>'
    )
    out = dict((mid, u) for mid, u in extract_maps(md, base_url=BASE))
    urls = set(out.values())
    assert any(
        u.endswith("/maps/USA-E-XChannel-1.jpg") for u in urls
    )  # hi-res, not -1m
    assert any(u.endswith("/maps/USA-E-XChannel-XII.jpg") for u in urls)
    assert not any(u.endswith("m.jpg") for u in urls)  # no low-res leak


def test_extract_maps_markdown_form_still_works():
    md = "[Map VIII](https://example.com/maps/m8.jpg)"
    urls = {u for _, u in extract_maps(md, base_url=BASE)}
    assert "https://example.com/maps/m8.jpg" in urls


def test_reference_footprint_paragraph_numbers():
    paras = [
        Paragraph(absolute_number=5, text="The assault began. (See Map VIII.)"),
        Paragraph(absolute_number=9, text="Nothing relevant here."),
        Paragraph(absolute_number=12, text="As shown on Map VIII, the beach..."),
    ]
    refs = _map_reference_footprint("Map VIII", paras)
    assert [r["paragraph_number"] for r in refs] == [5, 12]
    assert all("paragraph_number" in r and r.get("text") for r in refs)


def test_footprint_word_boundary_no_substring_bleed():
    # "Map 1" must NOT match "Map 11"
    paras = [Paragraph(absolute_number=1, text="see Map 11 for details")]
    assert _map_reference_footprint("Map 1", paras) == []


def test_iter_html_anchors_redos_safe():
    import time

    bad = "<a href=x>" * 20000
    t = time.time()
    list(_iter_html_anchors(bad))
    assert time.time() - t < 5.0
