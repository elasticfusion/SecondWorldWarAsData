"""Guard: the parser must capture raw-HTML <a href=...jpg> image/map anchors that pandoc passes
through verbatim (HyperWar maps + thumbnail-linked figures).

Chapter-8: 11 maps (3 embedded -Nm linked to -N, 8 link-only -VII..XIV) + 27 thumbnail-linked
figures (<a href=img/-N.jpg><img src=icn/-N.jpg>) were ALL missed because the old sub-extractors
only matched markdown ![]()/Joplin forms, not raw <a href><img>. This guard pins the recovery.
"""

from src.parser import extract_images

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/"


def _urls(text):
    return {(u or r) for (t, r, a, u) in extract_images(text, base_url=BASE)}


def test_embedded_linked_map_uses_fullres_href():
    # thumbnail -1m linked to full-res -1: capture the full-res href
    md = (
        '<a href="maps/USA-E-XChannel-1.jpg"><img src="maps/USA-E-XChannel-1m.jpg"></a>'
    )
    urls = _urls(md)
    assert any(u.endswith("/maps/USA-E-XChannel-1.jpg") for u in urls)


def test_link_only_map_is_captured():
    # link-only roman-numeral map (no <img>)
    md = '<a href="maps/USA-E-XChannel-XII.jpg" target="mapwindow">Map XII</a>'
    urls = _urls(md)
    assert any(u.endswith("/maps/USA-E-XChannel-XII.jpg") for u in urls)


def test_thumbnail_linked_figure_captured():
    md = '<a href="img/USA-E-XChannel-35.jpg"><img src="icn/USA-E-XChannel-35.jpg"></a>'
    urls = _urls(md)
    assert any(u.endswith("/img/USA-E-XChannel-35.jpg") for u in urls)


def test_non_image_anchor_ignored():
    # a plain prose hyperlink (not an image asset) must NOT be captured as an image
    md = '<a href="https://example.com/page.html">some text</a>'
    assert _urls(md) == set()


def test_absolute_href_kept_as_is():
    md = '<a href="https://cdn.example.com/map9.png">Map 9</a>'
    assert "https://cdn.example.com/map9.png" in _urls(md)


def test_malformed_unclosed_anchors_are_redos_safe():
    """Many unclosed <a tags (malformed untrusted HTML) must NOT cause quadratic backtracking.
    The bounded body regex ({0,4000}?) keeps this linear; a prior (.*?) was O(n^2) (~39s at 240KB).
    """
    import time

    bad = "<a href=x>" * 20000  # ~200KB of unclosed anchors (no </a>, no image ext)
    start = time.time()
    out = extract_images(bad, base_url="http://x/")
    elapsed = time.time() - start
    assert elapsed < 5.0, f"ReDoS: {elapsed:.1f}s on 200KB malformed input"
    assert out == []  # nothing valid to capture
