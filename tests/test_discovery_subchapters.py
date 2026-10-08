"""Regression: content discovery must find ALL sub-chapter letters, not just a-f.

A hardcoded ['a'..'f'] loop in _find_content_files silently dropped ~90 real source
sub-chapters (e.g. chapter6g 'The German Attack Toward Rocherath and Krinkelt') corpus-wide.
"""

from src.discovery import _find_content_files


def test_discovers_subsections_past_f(tmp_path):
    chdir = tmp_path / "chapter6"
    chdir.mkdir()
    for letter in "abcdefghij":
        (chdir / f"chapter6{letter}-content.md").write_text("x", encoding="utf-8")
    found = _find_content_files(chdir, "6")
    assert sorted(found.keys()) == list("abcdefghij")
    # the previously-dropped tail is present
    for letter in "ghij":
        assert letter in found


def test_single_content_file_still_works(tmp_path):
    chdir = tmp_path / "chapter9"
    chdir.mkdir()
    (chdir / "chapter9-content.md").write_text("x", encoding="utf-8")
    found = _find_content_files(chdir, "9")
    assert list(found.keys()) == [""]


def test_no_cross_chapter_bleed(tmp_path):
    # chapter1 discovery must not pick up chapter10/11/... files (prefix collision)
    chdir = tmp_path / "chapter1"
    chdir.mkdir()
    (chdir / "chapter1a-content.md").write_text("x", encoding="utf-8")
    (chdir / "chapter10a-content.md").write_text(
        "x", encoding="utf-8"
    )  # must NOT match
    found = _find_content_files(chdir, "1")
    assert sorted(found.keys()) == ["a"]
