"""Phase-0 security-gate observability: anomalies must be LOUD (no swallowed alerts)."""

import logging
from pathlib import Path

from src.ingestion import av_scan, media_detection, review_reject, prestage
from src.ingestion.av_scan import ScanResult, Verdict, AvOutcome


def test_av_scanner_unavailable_is_loud_warning_and_quarantines(caplog):
    out = AvOutcome()
    r = ScanResult("book.pdf", Verdict.ERROR, detail="timeout")

    class _S3:
        def copy_object(self, **k): ...
        def delete_object(self, **k): ...
        def put_object(self, **k): ...
        def head_object(self, **k):
            return {"Metadata": {}}

    with caplog.at_level(logging.WARNING, logger="src.ingestion.av_scan"):
        av_scan._gate_result(r, out, _S3(), "b", "us-east-1")
    assert (
        "OBSERV av.verdict" in caplog.text
        and "result=scanner_unavailable" in caplog.text
    )
    assert "fail_closed" in caplog.text
    assert "book.pdf" in out.quarantined_keys  # fail-closed


def test_av_infected_is_error_level(caplog):
    out = AvOutcome()
    r = ScanResult("mal.pdf", Verdict.INFECTED, signature="Eicar")

    class _S3:
        def copy_object(self, **k): ...
        def delete_object(self, **k): ...
        def put_object(self, **k): ...
        def head_object(self, **k):
            return {"Metadata": {}}

    with caplog.at_level(logging.ERROR, logger="src.ingestion.av_scan"):
        av_scan._gate_result(r, out, _S3(), "b", "us-east-1")
    assert "OBSERV av.verdict" in caplog.text and "result=infected" in caplog.text


def test_media_unsniffable_is_loud(tmp_path, caplog):
    # a .docx (ZIP) -> no magic signature in _MAGIC_SIGNATURES -> unsniffable -> WARNING
    f = tmp_path / "x.docx"
    f.write_bytes(b"PK\x03\x04rest-of-zip")
    with caplog.at_level(logging.WARNING, logger="src.ingestion.media_detection"):
        v = media_detection.detect_media_mismatch(f)
    assert v.is_mismatch is False
    assert (
        "OBSERV media.checked" in caplog.text and "verdict=unsniffable" in caplog.text
    )


def test_media_mismatch_is_loud(tmp_path, caplog):
    # .pdf extension but HTML bytes -> mismatch -> WARNING + reject
    f = tmp_path / "x.pdf"
    f.write_bytes(b"<!doctype html><html>")
    with caplog.at_level(logging.WARNING, logger="src.ingestion.media_detection"):
        v = media_detection.detect_media_mismatch(f)
    assert v.is_mismatch is True
    assert "verdict=mismatch" in caplog.text


def test_review_queued_fires(caplog):
    class _S3:
        def put_object(self, **k): ...

    with caplog.at_level(logging.WARNING, logger="src.ingestion.review_reject"):
        review_reject.reject_to_review(
            _S3(),
            "b",
            "contentrepository/x/x.html",
            "x",
            "bad type",
            category="media-mismatch",
        )
    assert (
        "OBSERV review.queued" in caplog.text
        and "category=media-mismatch" in caplog.text
    )


def test_review_queue_failed_is_loud(caplog):
    class _S3:
        def put_object(self, **k):
            raise RuntimeError("s3 down")

    with caplog.at_level(logging.WARNING, logger="src.ingestion.review_reject"):
        review_reject.reject_to_review(_S3(), "b", "k", "x", "r", category="intake")
    assert "OBSERV review.queue_failed" in caplog.text


def test_prestage_expansion_summary_fires(tmp_path, caplog):
    import zipfile

    arc = tmp_path / "a.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("a.txt", "hello")
        z.writestr("b.txt", "world")
    dest = tmp_path / "out"
    with caplog.at_level(logging.INFO, logger="src.ingestion.prestage"):
        prestage.expand_archive(arc, dest)
    assert "OBSERV prestage.expanded" in caplog.text and "members=2" in caplog.text
