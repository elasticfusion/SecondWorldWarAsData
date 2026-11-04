"""Guard: a chapter ``-meta.yaml`` sidecar is an expected upload, not an "unrecognized type".

XChannel ch8 e2e: ``chapter8-meta.yaml`` was rejected to needs-review/unrecognized-type with a
WARNING on every upload, although Phase 1 (book-scoped prefix sync) consumed it correctly.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")

from lambda_handlers import trigger_handler as th  # noqa: E402


def _run(keys):
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        out = th._content_keys(keys)
    return out, rej


def test_meta_yaml_sidecar_not_rejected_nor_queued():
    out, rej = _run(
        [
            "contentrepository/XChannel/chapter8/chapter8-meta.yaml",
            "contentrepository/XChannel/chapter8/CHAPTER9-META.YML",
            "contentrepository/XChannel/chapter8/chapter8a-content.md",
        ]
    )
    assert out == ["contentrepository/XChannel/chapter8/chapter8a-content.md"]
    rej.assert_not_called()


def test_other_yaml_still_rejected_to_review():
    out, rej = _run(["contentrepository/XChannel/chapter8/notes.yaml"])
    assert out == []
    rej.assert_called_once()
