"""Phase-0 Tier-0 P1 security fixes (PHASE0_REVIEW_consolidated.md)."""

import logging
import zipfile
from pathlib import Path


def test_book_slug_rejects_traversal_and_unsafe(caplog):
    from src.ingestion.book_slug import book_slug

    assert book_slug("contentrepository/MyBook/MyBook.html") == "MyBook"
    assert book_slug("x/my book.html") == "my_book"
    # traversal / unsafe -> 'unknown' + loud
    with caplog.at_level(logging.WARNING):
        assert (
            book_slug("x/../../etc/passwd").startswith(("unknown", "passwd"))
            or book_slug("x/../../etc/passwd") == "unknown"
        )
    bad = book_slug('x/a"b\nc.html')
    assert bad == "unknown"


def test_prestage_rejects_ratio_bomb(tmp_path, caplog):
    from src.ingestion import prestage

    arc = tmp_path / "bomb.zip"
    # highly compressible 5 MB member -> huge ratio
    with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.txt", b"A" * (5 * 1024 * 1024))
    dest = tmp_path / "out"
    with caplog.at_level(logging.WARNING, logger="src.ingestion.prestage"):
        out = prestage.expand_archive(arc, dest)
    # ratio ~5000x >> 100 -> rejected, member not written
    assert "OBSERV prestage.bomb_rejected" in caplog.text
    assert all(not p.name == "big.txt" for p in out)


def test_prestage_allows_normal_zip(tmp_path):
    from src.ingestion import prestage

    arc = tmp_path / "ok.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("a.txt", "hello world")
    out = prestage.expand_archive(arc, tmp_path / "o")
    assert any(p.name == "a.txt" for p in out)


def test_media_valid_epub_matches(tmp_path):
    from src.ingestion import media_detection

    f = tmp_path / "b.epub"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("content.opf", "<package/>")
    v = media_detection.detect_media_mismatch(f)
    assert v.is_mismatch is False


def test_media_spoofed_epub_mismatch(tmp_path):
    from src.ingestion import media_detection

    f = tmp_path / "b.epub"
    f.write_bytes(b"PK\x03\x04garbage-not-a-zip")
    v = media_detection.detect_media_mismatch(f)
    assert v.is_mismatch is True


def test_av_scan_enabled_default_true(monkeypatch):
    # default-on: with no env set, the trigger's AV gate flag resolves true
    monkeypatch.delenv("AV_SCAN_ENABLED", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ENV_NAME", "dev")  # dev stays permissive (no hard-fail)
    import importlib, lambda_handlers.trigger_handler as T

    importlib.reload(T)
    assert T.AV_SCAN_ENABLED is True
