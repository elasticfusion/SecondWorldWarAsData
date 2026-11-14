"""The trigger records an OCR submission's chunks (and the upload's optional validation URL)
BEFORE it submits any job, so the merge can tell a partial book from a whole one."""

import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")


def _submit(chunks, metadata):
    import lambda_handlers.trigger_handler as th

    events = []
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": metadata}
    s3.put_object.side_effect = lambda **k: events.append(
        ("put", k["Key"], k.get("Body"))
    )
    s3.delete_object.side_effect = lambda **k: events.append(("delete", k["Key"], None))
    batch = MagicMock()
    batch.submit_job.side_effect = lambda **k: events.append(
        ("submit", k["jobName"], None)
    )
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=s3),
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_media_mismatch", return_value=False),
        patch.object(th, "_claim_or_reclaim_ocr", return_value=True),
        patch.object(th, "_is_structured_reference", return_value=False),
        patch.object(th, "_ocr_chunks", return_value=chunks),
    ):
        ok = th._submit_ocr("contentrepository/CXA/CXA.pdf")
    return ok, events


def test_the_manifest_is_written_before_any_job_is_submitted():
    ok, events = _submit(
        ["1-50", "51-80"],
        {"validation-url": "https://www.ibiblio.org/hyperwar/x/index.html"},
    )
    assert ok
    kinds = [e[0] for e in events]
    assert kinds.index("put") < kinds.index("submit")
    put = next(e for e in events if e[0] == "put")
    assert put[1] == "ocr-output/CXA/.chunks.json"
    body = json.loads(put[2])
    assert body["chunks"] == ["chunk-p0001-0050", "chunk-p0051-0080"]
    assert body["source_key"] == "contentrepository/CXA/CXA.pdf"
    assert body["validation_url"] == "https://www.ibiblio.org/hyperwar/x/index.html"
    # a re-OCR never deletes the previous merge claim (it may belong to a merge still running);
    # the new submission's merge takes it over
    assert not [e for e in events if e[0] == "delete"]


def test_no_validation_url_means_an_empty_one():
    _, events = _submit([""], {})
    body = json.loads(next(e for e in events if e[0] == "put")[2])
    assert body["chunks"] == [""] and body["validation_url"] == ""


def test_an_unusable_validation_url_is_dropped_loudly():
    import lambda_handlers.trigger_handler as th

    with patch.object(th, "observe_error") as alert:
        _, events = _submit([""], {"validation-url": "http://169.254.169.254/latest"})
    body = json.loads(next(e for e in events if e[0] == "put")[2])
    assert body["validation_url"] == ""
    assert alert.call_args[0][:2] == ("ocr", "validation_url_rejected")


def test_a_manifest_write_failure_submits_nothing():
    import lambda_handlers.trigger_handler as th

    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {}}
    s3.put_object.side_effect = RuntimeError("s3 down")
    batch = MagicMock()
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=s3),
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_media_mismatch", return_value=False),
        patch.object(th, "_claim_or_reclaim_ocr", return_value=True),
        patch.object(th, "_is_structured_reference", return_value=False),
        patch.object(th, "_ocr_chunks", return_value=[""]),
        patch.object(th, "dynamo") as dynamo,
    ):
        assert th._submit_ocr("contentrepository/CXA/CXA.pdf") is False
    batch.submit_job.assert_not_called()
    dynamo.delete_item.assert_called_once()  # the claim is released for a retry
