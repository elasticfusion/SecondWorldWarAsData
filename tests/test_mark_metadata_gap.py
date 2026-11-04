"""DRY P2.7 guard: the 3-way _mark_metadata_gap (ocr_merge_handler/phase0_convert/phase0_video) now
delegates to review_reject.mark_metadata_gap. Pin the shared marker + fail-safe + that all 3
delegate (video with its title-less note)."""

import src.ingestion.review_reject as rr


def test_mark_metadata_gap_calls_reject(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rr,
        "reject_to_review",
        lambda s3, bucket, key, book, note, **k: calls.append(
            (bucket, key, book, note, k)
        ),
    )
    rr.mark_metadata_gap("s3", "mybucket", "k.md", "Book1", "us-east-1")
    assert len(calls) == 1
    bucket, key, book, note, kw = calls[0]
    assert (bucket, key, book) == ("mybucket", "k.md", "Book1")
    assert kw["category"] == "metadata" and kw["region"] == "us-east-1"
    assert "metadata not yet extracted" in note


def test_mark_metadata_gap_fail_safe(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(rr, "reject_to_review", boom)
    # must not raise
    rr.mark_metadata_gap("s3", "b", "k", "Book", "r")  # no exception


def test_custom_note_passthrough(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        rr, "reject_to_review", lambda s3, b, k, bk, note, **kw: seen.update(note=note)
    )
    rr.mark_metadata_gap("s3", "b", "k", "Bk", "r", note="custom gap note")
    assert seen["note"] == "custom gap note"


def test_all_three_callers_delegate(monkeypatch):
    seen = []
    monkeypatch.setattr(rr, "mark_metadata_gap", lambda *a, **k: seen.append((a, k)))
    import lambda_handlers.ocr_merge_handler as ocr
    import phase0_convert as pc
    import phase0_video as pv

    ocr._mark_metadata_gap("s3", "k1", "B")
    pc._mark_metadata_gap("s3", "k2", "B")
    pv._mark_metadata_gap("s3", "k3", "B")
    assert len(seen) == 3
    # video passes a title-less note
    assert any("author/series/license empty" in (k.get("note") or "") for _a, k in seen)
