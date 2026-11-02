"""HTML JavaScript stripping (owner policy: strip JS when saving HTML). _strip_html_boilerplate
removes <script>/<noscript> AND inline on*= handlers + javascript:/vbscript: URLs. Single-source
(all phases use this one converter). Content + legit links preserved.
"""

import pytest

from src.ingestion.text_converters import _strip_html_boilerplate


@pytest.mark.parametrize(
    "html,gone",
    [
        ('<p onclick="steal()">x</p>', "onclick"),
        ('<img src="a.jpg" onerror="xss()">', "onerror"),
        ('<body onload="go()">x</body>', "onload"),
        ('<a href="javascript:evil()">l</a>', "javascript:"),
        ('<a href="vbscript:evil()">l</a>', "vbscript:"),
        ('<a href="java\tscript:evil()">l</a>', "evil"),  # obfuscated scheme
        ("<script>bad()</script><p>ok</p>", "bad()"),
    ],
)
def test_inline_js_stripped(html, gone):
    assert gone not in _strip_html_boilerplate(html)


def test_legit_content_and_links_preserved():
    out = _strip_html_boilerplate(
        '<p>real text</p><a href="https://ok.com/x.jpg">link</a>'
    )
    assert "real text" in out and "https://ok.com/x.jpg" in out


def test_failsafe_returns_something_on_bad_input():
    # never raises; returns a string even for malformed input
    assert isinstance(_strip_html_boilerplate("<<<not html"), str)


def test_nul_byte_obfuscation_stripped():
    # java\x00script: must not survive (browsers strip NUL and re-collapse to javascript:)
    assert "evil" not in _strip_html_boilerplate(
        '<a href="java\x00script:evil()">l</a>'
    )


def test_url_extractor_uses_shared_sanitizer_no_js_leak(tmp_path):
    # same-code-all-phases: the url_extractor HTML->md path must ALSO strip inline JS (it routes
    # through the shared _strip_html_boilerplate), not just the pandoc ingestion path.
    from src.url_extractor import URLExtractor

    x = URLExtractor(output_dir=str(tmp_path))
    md = x.html_to_markdown(
        '<a href="javascript:evil()">l</a><a href="vbscript:bad()">v</a><p onclick="x()">t</p>'
    )
    assert "javascript:" not in md and "vbscript:" not in md and "onclick" not in md
