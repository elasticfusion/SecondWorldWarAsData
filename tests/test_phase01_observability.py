"""Phase 0/1 canonical OBSERV domain events (E2E observability-gap fix)."""

import logging
import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def test_phase0_skipped_unsupported_ext(caplog):
    import phase0_convert as P0

    with caplog.at_level(logging.INFO, logger="phase0_convert"):
        out = P0.convert_key("contentrepository/Book/thing.xyz")  # unsupported ext
    assert out == ""
    assert "OBSERV phase0.skipped reason=unsupported_ext" in caplog.text


def test_phase0_main_no_key(caplog, monkeypatch):
    import phase0_convert as P0

    monkeypatch.delenv("CONVERT_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="phase0_convert"):
        rc = P0.main()
    assert rc == 2
    assert "OBSERV phase0.error reason=no_convert_key" in caplog.text


def test_phase1_observe_book_parsed(tmp_path, caplog):
    import json
    import phase1_parse as P1

    bo = tmp_path / "USA-E-XChannel-8"
    bo.mkdir()
    (bo / "chapter1full-parsed.json").write_text(
        json.dumps({"paragraphs": [{"text": "x"}] * 263})
    )
    with caplog.at_level(logging.INFO, logger=P1.__name__):
        P1._observe_phase1_book(
            "USA-E-XChannel-8", [object()], bo, logging.getLogger(P1.__name__)
        )
    assert "OBSERV phase1.parsed book=USA-E-XChannel-8" in caplog.text
    assert "paragraphs=263" in caplog.text


def test_phase1_observe_book_skipped_zero(tmp_path, caplog):
    import phase1_parse as P1

    bo = tmp_path / "EmptyBook"
    bo.mkdir()  # no parsed files
    with caplog.at_level(logging.WARNING, logger=P1.__name__):
        P1._observe_phase1_book(
            "EmptyBook", [object()], bo, logging.getLogger(P1.__name__)
        )
    assert "OBSERV phase1.skipped book=EmptyBook reason=no_parsed_output" in caplog.text
