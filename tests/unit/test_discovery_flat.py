"""Tests for content discovery (A2: flat-book-markdown fallback)."""

from pathlib import Path

from src.discovery import discover_content_structure


def test_chapter_structure_discovered(tmp_path):
    book = tmp_path / "MyBook" / "chapter1"
    book.mkdir(parents=True)
    (book / "chapter1-content.md").write_text("# Chapter 1\nprose", encoding="utf-8")
    (book / "chapter1-meta.yaml").write_text(
        'book: "MyBook"\nchapter_number: "1"\nchapter_title: "One"\n', encoding="utf-8"
    )
    structure = discover_content_structure(tmp_path)
    assert "MyBook" in structure
    assert len(structure["MyBook"]) == 1


def test_a2_flat_book_markdown_is_single_chapter(tmp_path):
    """A2: {book}/{book}.md with NO chapter dirs -> single-chapter book."""
    book = tmp_path / "FlatBook"
    book.mkdir(parents=True)
    (book / "FlatBook.md").write_text("# Flat\nsome prose", encoding="utf-8")
    structure = discover_content_structure(tmp_path)
    assert "FlatBook" in structure
    assert len(structure["FlatBook"]) == 1
    assert structure["FlatBook"][0].chapter_number == 1


def test_a2_flat_prefers_content_md(tmp_path):
    book = tmp_path / "B2"
    book.mkdir(parents=True)
    (book / "notes.md").write_text("notes", encoding="utf-8")
    (book / "chapter1-content.md").write_text("real content", encoding="utf-8")
    structure = discover_content_structure(tmp_path)
    assert "B2" in structure
    cg = structure["B2"][0]
    assert cg.content_files[""].name == "chapter1-content.md"


def test_empty_book_dir_ignored(tmp_path):
    (tmp_path / "Empty").mkdir(parents=True)
    structure = discover_content_structure(tmp_path)
    assert "Empty" not in structure
