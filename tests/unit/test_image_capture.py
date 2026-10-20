"""Regression: maps/images capture from ibiblio (clean XChannel run showed 1/page + maps=0).

Three bugs fixed:
  (1) parser.extract_images deduped on alt_text; ibiblio images all have alt="" -> collapsed
      10 images to 1. Now dedupes on image IDENTITY (url/resource_id).
  (2) ibiblio wraps each thumbnail <img> in <a href> to the FULL-RES image; html2text yields
      the combined shape [![alt](IMG_URL)](LINK_URL). extract_images now captures the inner
      full-res IMG_URL (new HTML-derived combined branch).
  (3) the importer now emits the parent <a href> full-res asset (img/.. , maps/..) instead of
      the thumbnail (icn/.. , ..m.jpg); maps under /maps/ then classify as "map".
"""

from src.parser import extract_images
from src.extraction.images import _classify_content_type


def test_empty_alt_images_not_collapsed():
    """Multiple images sharing an empty alt must all survive (dedupe on URL, not alt)."""
    md = (
        "![](https://ex.org/USA-E-XChannel/img/a.jpg)\n"
        "![](https://ex.org/USA-E-XChannel/img/b.jpg)\n"
        "![](https://ex.org/USA-E-XChannel/img/c.jpg)\n"
    )
    imgs = extract_images(md)
    urls = {u for _t, u, _alt, _e in imgs}
    assert len(imgs) == 3
    assert urls == {
        "https://ex.org/USA-E-XChannel/img/a.jpg",
        "https://ex.org/USA-E-XChannel/img/b.jpg",
        "https://ex.org/USA-E-XChannel/img/c.jpg",
    }


def test_html_combined_thumbnail_linked_to_fullres_captures_inner_url():
    """[![alt](FULL)](<THUMB>) — the html2text shape for an <img> wrapped in a link — must
    capture the inner FULL-res image url, and multiple such images must not collapse."""
    md = (
        "[![USA-E-XChannel-4](https://ex.org/USA-E-XChannel/maps/USA-E-XChannel-4.jpg)]"
        "(<maps/USA-E-XChannel-4m.jpg>)\n"
        "[![USA-E-XChannel-62](https://ex.org/USA-E-XChannel/img/USA-E-XChannel-62.jpg)]"
        "(<img/USA-E-XChannel-62.jpg>)\n"
    )
    imgs = extract_images(md)
    urls = sorted(u for _t, u, _alt, _e in imgs)
    assert urls == [
        "https://ex.org/USA-E-XChannel/img/USA-E-XChannel-62.jpg",
        "https://ex.org/USA-E-XChannel/maps/USA-E-XChannel-4.jpg",
    ]
    # the maps/ full-res url classifies as a map
    maps = [u for _t, u, _a, _e in imgs if _classify_content_type(_a, u) == "map"]
    assert maps == ["https://ex.org/USA-E-XChannel/maps/USA-E-XChannel-4.jpg"]


def test_importer_prefers_fullres_href_over_thumbnail_src():
    """The importer must emit the parent <a href> full-res asset, not the <img> thumbnail."""
    import importlib.util
    import sys

    import pytest

    BeautifulSoup = pytest.importorskip("bs4").BeautifulSoup
    spec = importlib.util.spec_from_file_location(
        "imp_img", "scripts/import_hyperwar_html.py"
    )
    imp = importlib.util.module_from_spec(spec)
    sys.modules["imp_img"] = imp
    try:
        spec.loader.exec_module(imp)
    except SystemExit:
        pass

    url = "https://ex.org/USA-E-XChannel/USA-E-XChannel-9.html"
    html = (
        "<html><body>"
        '<a href="maps/USA-E-XChannel-4.jpg"><img src="maps/USA-E-XChannel-4m.jpg"></a>'
        '<a href="img/USA-E-XChannel-62.jpg"><img src="icn/USA-E-XChannel-62.jpg"></a>'
        "</body></html>"
    )
    md = imp.convert_html_to_markdown(BeautifulSoup(html, "html.parser"), url)
    imgs = extract_images(md)
    urls = {u for _t, u, _a, _e in imgs}
    # full-res (maps/, img/) present; thumbnails (…m.jpg, icn/) are NOT the captured url
    assert any(u.endswith("/maps/USA-E-XChannel-4.jpg") for u in urls)
    assert any(u.endswith("/img/USA-E-XChannel-62.jpg") for u in urls)
    assert not any("m.jpg" in u and "/maps/" in u for u in urls)
    assert not any("/icn/" in u for u in urls)
