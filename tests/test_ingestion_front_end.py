"""Tests for the ingestion front-end: source metadata + media detection."""

from pathlib import Path

import pytest

from src.ingestion.media_detection import (
    SUPPORTED_MEDIA,
    build_source_metadata,
    detect_media_type,
)
from src.ingestion.source_metadata import SourceMetadata, compute_checksum

PDF_BYTES = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
HTML_BYTES = b"<!DOCTYPE html>\n<html><body>hi</body></html>"


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- media type detection ------------------------------------------------


def test_detect_by_extension(tmp_path: Path) -> None:
    assert detect_media_type(_write(tmp_path, "a.pdf", PDF_BYTES)) == "pdf"
    assert detect_media_type(_write(tmp_path, "a.html", HTML_BYTES)) == "html"
    assert detect_media_type(_write(tmp_path, "a.png", PNG_BYTES)) == "image"


def test_detect_moving_image_is_recognized(tmp_path: Path) -> None:
    # Extension recognizes it as moving_image even with unknown bytes.
    path = _write(tmp_path, "clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    assert detect_media_type(path) == "moving_image"


def test_detect_unknown_is_unsupported(tmp_path: Path) -> None:
    path = _write(tmp_path, "mystery.xyz", b"random-bytes-here")
    assert detect_media_type(path) == "unsupported"


def test_content_type_hint(tmp_path: Path) -> None:
    # No extension; rely on the supplied content-type header.
    path = _write(tmp_path, "download", PDF_BYTES)
    assert (
        detect_media_type(path, content_type="application/pdf; charset=binary") == "pdf"
    )


def test_magic_bytes_override_wrong_extension(tmp_path: Path) -> None:
    # A PDF mislabeled with an .html extension: bytes win.
    path = _write(tmp_path, "mislabeled.html", PDF_BYTES)
    assert detect_media_type(path) == "pdf"


# --- source metadata record ----------------------------------------------


def test_build_metadata_supported_pdf(tmp_path: Path) -> None:
    path = _write(tmp_path, "doc.pdf", PDF_BYTES)
    meta = build_source_metadata("01SRC", path, acquisition_method="local")
    assert meta.media_type == "pdf"
    assert meta.supported is True
    assert meta.source_id == "01SRC"
    assert meta.checksum and meta.checksum.startswith("sha256:")
    assert meta.notes == ""


def test_build_metadata_unsupported_is_flagged_not_raised(tmp_path: Path) -> None:
    path = _write(tmp_path, "clip.mov", b"\x00\x00\x00\x18ftypqt  ")
    meta = build_source_metadata("01SRC", path)
    assert meta.media_type == "moving_image"
    assert meta.supported is False
    assert "not yet supported" in meta.notes


def test_build_metadata_unknown_media(tmp_path: Path) -> None:
    path = _write(tmp_path, "x.dat", b"nope")
    meta = build_source_metadata("01SRC", path, with_checksum=False)
    assert meta.media_type == "unsupported"
    assert meta.supported is False
    assert meta.checksum is None


def test_build_metadata_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_source_metadata("01SRC", tmp_path / "absent.pdf")


def test_round_trip_serialization(tmp_path: Path) -> None:
    path = _write(tmp_path, "doc.pdf", PDF_BYTES)
    meta = build_source_metadata("01SRC", path, acquisition_url="http://x/y")
    restored = SourceMetadata.from_dict(meta.to_dict())
    assert restored == meta
    # from_dict tolerates extra keys.
    payload = meta.to_dict()
    payload["unexpected"] = "ignored"
    assert SourceMetadata.from_dict(payload) == meta


def test_supported_media_membership() -> None:
    assert "pdf" in SUPPORTED_MEDIA
    assert "moving_image" not in SUPPORTED_MEDIA


def test_checksum_stable_and_chunked(tmp_path: Path) -> None:
    path = _write(tmp_path, "big.bin", b"abc" * 500_000)
    assert compute_checksum(path) == compute_checksum(path)
