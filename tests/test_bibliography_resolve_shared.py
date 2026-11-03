"""DRY P2.4 guard: the 3 _resolve_* share _resolve_with_alt_title (title -> alt_title fallback) and
the 2 _find_in_* share _find_in_native_source (own-flag gate + candidate verify + source label).
"""

import src.enrichment.bibliography_resolver as br


def test_resolve_with_alt_title_fallback():
    calls = []

    def search(title, author):
        calls.append(title)
        return "HIT" if title == "Alt" else None  # primary misses, alt hits

    cit = {"title": "Primary", "alt_title": "Alt", "author": ["Cole"]}
    assert br._resolve_with_alt_title(cit, search) == "HIT"
    assert calls == ["Primary", "Alt"]  # tried primary then alt


def test_resolve_with_alt_title_no_retry_when_same():
    calls = []
    cit = {"title": "T", "alt_title": "T", "author": ["X"]}  # alt == title -> no retry
    assert br._resolve_with_alt_title(cit, lambda t, a: calls.append(t)) is None
    assert calls == ["T"]


def test_resolve_wrappers_use_scaffold(monkeypatch):
    # Each wrapper calls _resolve_with_alt_title.
    seen = []
    monkeypatch.setattr(
        br, "_resolve_with_alt_title", lambda cit, search: seen.append(cit.get("title"))
    )
    cit = {"title": "Breakout", "author": ["Blumenson"]}
    br._resolve_book(None, cit, None, {})
    br._resolve_article(None, cit, None, {})
    br._resolve_generic_entry(None, cit, None, {})
    assert seen == ["Breakout", "Breakout", "Breakout"]


def test_find_in_native_source_gate_and_label(monkeypatch):
    monkeypatch.setattr(
        br, "_collect_verified_candidates", lambda q, cands, gc, e: ["http://u1"]
    )
    # flag off -> []
    assert (
        br._find_in_native_source(
            {},
            "q",
            "t",
            "a",
            None,
            {"myflag": False},
            flag="myflag",
            search_candidates=lambda t, a: [],
            label="src",
        )
        == []
    )
    # flag on -> verified candidates tagged with label
    out = br._find_in_native_source(
        {},
        "q",
        "t",
        "a",
        None,
        {"myflag": True},
        flag="myflag",
        search_candidates=lambda t, a: [("c", "x")],
        label="src",
    )
    assert out == [("http://u1", "src")]


def test_archive_and_gutenberg_delegate(monkeypatch):
    seen = []
    monkeypatch.setattr(
        br,
        "_find_in_native_source",
        lambda *a, **k: seen.append((k["flag"], k["label"])) or [],
    )
    br._find_in_archive_org({}, "q", "t", "a", None, {})
    br._find_in_gutenberg({}, "q", "t", "a", None, {})
    assert seen == [
        ("search_archive_org", "archive_org"),
        ("search_gutenberg", "gutenberg"),
    ]
