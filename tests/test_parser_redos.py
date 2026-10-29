"""Regression: parser image/map/footnote extraction must be LINEAR-bounded on adversarial input.

Phase-1 review P1-A: '![' repetition in untrusted Phase-0 markdown caused O(n^2) backtracking
(40KB->4.7s, 160KB->74.7s, 500KB->520s). Bounded quantifiers + the 500KB cap make it ~linear.
This test FAILS if the catastrophic behavior ever returns (it caught an insufficient first fix).
"""

import time
from src import parser


def _elapsed(fn, text):
    t = time.time()
    fn(text)
    return time.time() - t


def test_extract_images_not_redos_on_adversarial():
    # 500KB of '![' (the cap worst case). Pre-fix this was ~520s; must now be well under 5s.
    adv = "![" * (500 * 1024 // 2)
    assert _elapsed(parser.extract_images, adv) < 5.0


def test_extract_maps_and_footnotes_bounded():
    adv = "[Map " * (200 * 1024 // 5)
    assert _elapsed(parser.extract_maps, adv) < 5.0
    advf = "<sup>[1](" * (200 * 1024 // 9)
    assert _elapsed(parser.extract_footnotes, advf) < 5.0


def test_extract_images_still_correct():
    txt = (
        "[![Omaha](:/res1)](https://ex.com/omaha.jpg)\n"
        "![Utah](https://ex.com/utah.png)\n"
        "![Joplin](:/res2)\n"
    )
    kinds = [i[0] for i in parser.extract_images(txt)]
    assert "combined" in kinds and "external" in kinds and "embedded" in kinds


def test_extract_maps_still_correct():
    assert parser.extract_maps("[Map 5](https://ex.com/map5.png)") == [
        ("5", "https://ex.com/map5.png")
    ]
