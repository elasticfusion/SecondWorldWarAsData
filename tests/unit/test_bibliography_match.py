"""Tests for bibliography multi-key dedup match (title -> archive-ref -> author)."""

from src.extraction.bibliography import _find_match, _match_keys, _index_entry


def _idx():
    return {"titles": {}, "refs": {}, "authors": {}}


def test_title_exact_and_fuzzy():
    idx = _idx()
    _index_entry(idx, {"title": "operation overlord", "ref": "", "author": ""}, "BIB1")
    assert _find_match({"title": "operation overlord"}, idx) == "BIB1"
    # 0.85 fuzzy near-duplicate
    assert _find_match({"title": "operation overlrd"}, idx) == "BIB1"


def test_archive_ref_fallback_when_title_differs():
    idx = _idx()
    _index_entry(
        idx,
        {"title": "after action report", "ref": "rg 407 entry 427", "author": ""},
        "BIB1",
    )
    # title won't match, but archive ref does
    m = _find_match(
        {"title": "totally different title", "ref": "rg 407 entry 427"}, idx
    )
    assert m == "BIB1"


def test_author_fallback_when_title_and_ref_differ():
    idx = _idx()
    _index_entry(
        idx,
        {"title": "the lorraine campaign", "ref": "", "author": "cole hugh m"},
        "BIB1",
    )
    m = _find_match({"title": "lorraine gpo", "ref": "", "author": "cole hugh m"}, idx)
    assert m == "BIB1"


def test_no_match_returns_none():
    idx = _idx()
    _index_entry(idx, {"title": "a", "ref": "r1", "author": "x"}, "BIB1")
    assert _find_match({"title": "z", "ref": "r2", "author": "y"}, idx) is None


def test_match_keys_extracts_ref_and_author():
    material = {
        "archive_reference_number": "RG 407, Entry 427",
        "citation": {"title": "Foo", "author": ["Cole, Hugh M."]},
    }
    keys = _match_keys(material, "Foo Bar")
    assert keys["title"] == "foo bar"
    assert keys["ref"] == "rg 407 entry 427"
    assert keys["author"] == "cole hugh m"


def test_legacy_flat_index_still_matches(tmp_path):
    """A legacy flat {norm_title: filename} index.json is read as 'titles'."""
    import json
    from src.extraction.bibliography import _load_index

    bib = tmp_path / "bibliography"
    bib.mkdir()
    (bib / "index.json").write_text(
        json.dumps({"operation overlord": "overlord_BIB1.json"}), encoding="utf-8"
    )
    idx = _load_index(bib)
    assert idx["titles"]["operation overlord"] == "overlord_BIB1.json"
    assert _find_match({"title": "operation overlord"}, idx) == "overlord_BIB1.json"
