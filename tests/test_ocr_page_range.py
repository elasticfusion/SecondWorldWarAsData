"""Tests for the 1-based-physical -> 0-based-Chandra page-range fix.

Chandra's ``--page-range`` is 0-based and matched against 0-based PDF page
indices (``chandra.input.parse_range_str``). The pipeline speaks 1-based
physical pages; ``submit_ocr_job._to_chandra_range`` converts only the value
handed to Chandra. Passing the 1-based spec straight through dropped the first
page of every range — this locks the fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "submit_ocr_job",
    str(Path(__file__).resolve().parents[1] / "scripts" / "submit_ocr_job.py"),
)
soj = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(soj)  # type: ignore[union-attr]


def test_single_page_shifts_to_zero_based():
    assert soj._to_chandra_range("7") == "6"
    assert soj._to_chandra_range("1") == "0"


def test_range_shifts_both_bounds():
    assert soj._to_chandra_range("1-2") == "0-1"
    assert soj._to_chandra_range("1-50") == "0-49"
    assert soj._to_chandra_range("21-34") == "20-33"
    assert soj._to_chandra_range("155-156") == "154-155"


def test_comma_list_shifts_each():
    assert soj._to_chandra_range("21-34,40") == "20-33,39"
    assert soj._to_chandra_range("1,5,9") == "0,4,8"


def test_non_numeric_returned_unchanged():
    assert soj._to_chandra_range("intro") == "intro"


def test_a_numeric_spec_that_cannot_be_converted_is_refused_not_sent_one_based():
    import pytest

    for bad in ("0-5", "5-3", "1-", "-4", "7-x"):
        with pytest.raises(ValueError):
            soj._to_chandra_range(bad)
    assert soj._to_chandra_range("1 - 5, 9") == "0-4,8"  # spacing is no error


def test_roundtrip_against_chandra_parser_covers_all_physical_pages():
    """Our 0-based output, parsed by Chandra, must cover exactly the intended
    physical pages as 0-based indices (physical N -> index N-1)."""
    import pytest

    # chandra (the OCR VLM lib) is only installed in the Chandra Docker image,
    # not in CI's deps — skip cleanly there; still validates where it exists.
    pytest.importorskip("chandra")
    from chandra.input import parse_range_str  # pylint: disable=import-error

    # Physical pages 1..50 -> indices 0..49, all present, none dropped/overshot.
    indices = parse_range_str(soj._to_chandra_range("1-50"))
    assert indices == list(range(0, 50))

    # The 2-page case that originally dropped page 1.
    assert parse_range_str(soj._to_chandra_range("1-2")) == [0, 1]

    # Single physical page 7 -> index 6.
    assert parse_range_str(soj._to_chandra_range("7")) == [6]


def test_chunk_dir_stays_one_based_physical():
    """The human-facing chunk dir must remain 1-based (only the Chandra arg
    shifts), so output layout / merge mapping are unaffected by the fix."""
    assert soj._chunk_dir("1-50") == "chunk-p0001-0050"
    assert soj._chunk_dir("155-156") == "chunk-p0155-0156"


# --- the trigger's automatic OCR (2026-11-09: it passed 1-based ranges straight to Chandra) -------


def test_the_shared_conversion_refuses_what_it_cannot_convert():
    import pytest

    from src.ingestion.ocr_chunk_manifest import chandra_page_range

    assert chandra_page_range("1-50") == "0-49"
    assert chandra_page_range("51-67") == "50-66"
    assert chandra_page_range("21-34, 40") == "20-33,39"
    for bad in ("", "0-5", "intro", "5-3", "1-", "-4", "a-b"):
        with pytest.raises(ValueError):
            chandra_page_range(bad)


def test_the_cli_and_the_trigger_use_one_conversion():
    from src.ingestion.ocr_chunk_manifest import chandra_page_range

    for spec in ("1", "7", "1-50", "51-67", "21-34,40"):
        assert soj._to_chandra_range(spec) == chandra_page_range(spec)


def test_the_trigger_hands_chandra_zero_based_ranges_and_keeps_one_based_dirs():
    """Cross-Channel Attack Chapter VIII: chunk "1-50" OCR'd PDF pages 2-51, so the chapter's opening
    page - "CHAPTER VIII" - was never read and page 52 was read twice."""
    import os
    from unittest.mock import MagicMock, patch

    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import lambda_handlers.trigger_handler as th

    batch = MagicMock()
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_media_mismatch", return_value=False),
        patch.object(th, "_claim_or_reclaim_ocr", return_value=True),
        patch.object(th, "_is_structured_reference", return_value=False),
        patch.object(th, "_validation_url", return_value=""),
        patch.object(th, "_ocr_chunks", return_value=["1-50", "51-67"]),
    ):
        assert th._submit_ocr("contentrepository/B/B.pdf") is True
    commands = [
        c.kwargs["containerOverrides"]["command"]
        for c in batch.submit_job.call_args_list
    ]
    assert [c[c.index("--page-range") + 1] for c in commands] == ["0-49", "50-66"]
    assert [c[1] for c in commands] == [
        "s3://buck/ocr-output/B/chunk-p0001-0050/",
        "s3://buck/ocr-output/B/chunk-p0051-0067/",
    ]  # the output directories stay 1-based physical: the merge's page numbers depend on it


def test_a_bad_chunk_range_submits_nothing_and_releases_the_claim():
    """Every chunk's job is built before the manifest and the first GPU job: one range that
    cannot be converted must not leave a half-submitted book (a manifest waiting forever for a
    chunk that never runs) or a held claim."""
    import os
    from unittest.mock import MagicMock, patch

    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import lambda_handlers.trigger_handler as th

    batch, dynamo = MagicMock(), MagicMock()
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch.object(th, "dynamo", dynamo),
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_media_mismatch", return_value=False),
        patch.object(th, "_claim_or_reclaim_ocr", return_value=True),
        patch.object(th, "_is_structured_reference", return_value=False),
        patch.object(th, "_validation_url", return_value=""),
        patch.object(th, "_ocr_chunks", return_value=["1-50", "0-10"]),
        patch("src.ingestion.ocr_chunk_manifest.write_manifest") as write_manifest,
    ):
        assert th._submit_ocr("contentrepository/B/B.pdf") is False
    write_manifest.assert_not_called()
    batch.submit_job.assert_not_called()
    dynamo.delete_item.assert_called_once_with(Key={"cache_key": "ocr#B"})
