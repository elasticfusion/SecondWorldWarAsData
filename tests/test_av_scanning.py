"""Tests for the AV / malicious-document scanning gate.

Demand-launched, binary-only, fail-closed, quarantine-not-delete, freeze-submitter
hook. No real AV engine runs — the Scanner is mocked.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.ingestion.av_scan import (
    AvOutcome,
    ScanResult,
    Verdict,
    freeze_submitter,
    scan_and_gate,
    select_binary_keys,
)

# --- binary gate (demand-launch selection) ---


def test_select_binary_keys_picks_only_binaries():
    keys = [
        "contentrepository/a/book.pdf",
        "contentrepository/a/photo.jpg",
        "contentrepository/a/clip.mp4",
        "contentrepository/a/sheet.xlsx",
        "contentrepository/a/notes.md",  # text -> excluded
        "contentrepository/a/page.html",  # text -> excluded
        "contentrepository/a/raw.txt",  # text -> excluded
    ]
    bins = select_binary_keys(keys)
    assert "contentrepository/a/book.pdf" in bins
    assert "contentrepository/a/photo.jpg" in bins
    assert "contentrepository/a/clip.mp4" in bins
    assert "contentrepository/a/sheet.xlsx" in bins
    assert not any(k.endswith((".md", ".html", ".txt")) for k in bins)


import pytest


@pytest.mark.parametrize(
    "ext",
    [
        # documents
        ".pdf",
        ".epub",
        ".docx",
        ".doc",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".pptx",
        ".ppt",
        ".rtf",
        # images (JPG + the rest)
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff",
        ".bmp",
        ".gif",
        ".webp",
        # video / audio
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".m4v",
        ".mp3",
        ".wav",
        ".m4a",
        # archive
        ".zip",
    ],
)
def test_every_binary_type_is_scanned(ext):
    """Every non-text binary (images incl. JPG, video, audio, office, archive)
    is selected for AV scanning."""
    assert select_binary_keys([f"c/file{ext}"]) == [f"c/file{ext}"]


@pytest.mark.parametrize("ext", [".txt", ".md", ".markdown", ".html", ".htm"])
def test_text_types_never_scanned(ext):
    """Inert text is never scanned (no practical virus risk in this pipeline)."""
    assert select_binary_keys([f"c/file{ext}"]) == []


def test_infected_image_quarantined():
    """A malicious image (JPG) hits torch/Chandra — must be caught + quarantined."""
    scanner = MagicMock()
    scanner.scan.return_value = [
        ScanResult("c/evil.jpg", Verdict.INFECTED, signature="Exploit.JPEG")
    ]
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {}}
    with patch("boto3.client", return_value=MagicMock()):
        out = scan_and_gate(s3, "buck", ["c/evil.jpg"], scanner)
    assert out.quarantined_keys == ["c/evil.jpg"]
    assert s3.copy_object.call_args.kwargs["Key"] == "quarantine/c/evil.jpg"


def test_text_only_batch_launches_no_scan():
    """Text-only -> scanner never called, nothing scanned, all keys clear."""
    scanner = MagicMock()
    s3 = MagicMock()
    out = scan_and_gate(
        s3, "buck", ["contentrepository/a/notes.md", "x/y.txt"], scanner
    )
    assert out.scanned is False
    scanner.scan.assert_not_called()
    assert out.all_clear is True
    assert set(out.clean_keys) == {"contentrepository/a/notes.md", "x/y.txt"}


# --- clean path ---


def test_clean_binary_proceeds():
    scanner = MagicMock()
    scanner.scan.return_value = [ScanResult("c/book.pdf", Verdict.CLEAN)]
    s3 = MagicMock()
    out = scan_and_gate(s3, "buck", ["c/book.pdf", "c/notes.md"], scanner)
    assert out.scanned is True
    assert out.all_clear is True
    assert "c/book.pdf" in out.clean_keys
    assert "c/notes.md" in out.clean_keys  # non-binary always clear
    s3.copy_object.assert_not_called()  # nothing quarantined


# --- infected path: quarantine + freeze ---


def test_infected_quarantined_and_freeze_hook_fires():
    scanner = MagicMock()
    scanner.scan.return_value = [
        ScanResult("c/evil.pdf", Verdict.INFECTED, signature="Eicar-Test")
    ]
    s3 = MagicMock()
    # submitter identity present -> freeze marker written
    s3.head_object.return_value = {"Metadata": {"api-key-id": "key-123"}}
    with patch("boto3.client", return_value=MagicMock()):
        out = scan_and_gate(s3, "buck", ["c/evil.pdf"], scanner)
    assert "c/evil.pdf" in out.quarantined_keys
    assert out.all_clear is False
    # moved to quarantine/ (copy + delete), not deleted outright
    s3.copy_object.assert_called_once()
    assert s3.copy_object.call_args.kwargs["Key"] == "quarantine/c/evil.pdf"
    s3.delete_object.assert_called_once()
    # freeze marker written for the identity
    put_keys = [c.kwargs.get("Key") for c in s3.put_object.call_args_list]
    assert any(k == "quarantine/frozen/key-123.json" for k in put_keys)
    # reject-to-review marker written under av-infected
    assert any("needs-review/av-infected/" in str(k) for k in put_keys)


def test_freeze_noop_without_identity():
    """S3-event upload (no principal) -> freeze hook records nothing, never raises."""
    s3 = MagicMock()
    freeze_submitter(s3, "buck", "", key="c/evil.pdf", signature="X")
    s3.put_object.assert_not_called()


# --- fail-closed ---


def test_scanner_error_fails_closed():
    scanner = MagicMock()
    scanner.scan.return_value = [
        ScanResult("c/x.pdf", Verdict.ERROR, detail="engine down")
    ]
    s3 = MagicMock()
    out = scan_and_gate(s3, "buck", ["c/x.pdf"], scanner)
    assert "c/x.pdf" in out.quarantined_keys  # unscannable -> quarantined
    assert out.all_clear is False
    s3.copy_object.assert_called_once()  # moved to quarantine


def test_scanner_raises_fails_closed():
    scanner = MagicMock()
    scanner.scan.side_effect = RuntimeError("boom")
    s3 = MagicMock()
    out = scan_and_gate(s3, "buck", ["c/x.pdf"], scanner)
    assert "c/x.pdf" in out.quarantined_keys
    assert out.all_clear is False


def test_unreported_binary_fails_closed():
    """Scanner silently omits a key -> treated as ERROR -> quarantined."""
    scanner = MagicMock()
    scanner.scan.return_value = [ScanResult("c/a.pdf", Verdict.CLEAN)]
    s3 = MagicMock()
    out = scan_and_gate(s3, "buck", ["c/a.pdf", "c/b.pdf"], scanner)
    assert "c/a.pdf" in out.clean_keys
    assert "c/b.pdf" in out.quarantined_keys


def test_mixed_batch_partitions_correctly():
    scanner = MagicMock()
    scanner.scan.return_value = [
        ScanResult("c/good.pdf", Verdict.CLEAN),
        ScanResult("c/bad.docx", Verdict.INFECTED, signature="Trojan"),
    ]
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {}}
    with patch("boto3.client", return_value=MagicMock()):
        out = scan_and_gate(
            s3, "buck", ["c/good.pdf", "c/bad.docx", "c/notes.md"], scanner
        )
    assert "c/good.pdf" in out.clean_keys
    assert "c/notes.md" in out.clean_keys
    assert out.quarantined_keys == ["c/bad.docx"]
