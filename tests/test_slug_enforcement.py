"""The slug is immutable (owner, 2026-11-11) - its enforcement at intake and in Phase 1:

* an upload whose name gives no safe slug is rejected, never processed as the shared `unknown`;
* a document already ingested under one slug is rejected when re-uploaded under another;
* a task is never launched for `unknown`;
* a chapter meta naming another book than its folder is overruled by the folder slug, loudly;
* the chapter meta carries the BookID.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")


@pytest.fixture
def th():
    # lazy import: see tests/test_trigger_meta_sidecar.py
    import lambda_handlers.trigger_handler as th

    return th


@pytest.fixture
def aws(th, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="buck")
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(th, "BUCKET", "buck")
        monkeypatch.setattr(th, "dynamo", table)
        yield s3, table


# --- the source -> slug binding -------------------------------------------------------------------


def test_a_document_binds_to_its_first_slug_once(th, aws):
    _, table = aws
    assert th._bind_source("sha256:abc", "Book_A") == ""
    assert th._bind_source("sha256:abc", "Book_A") == ""  # the same name again: fine
    assert th._bind_source("sha256:abc", "Book_B") == "Book_A"  # a second name: refused
    assert (
        table.get_item(Key={"cache_key": "source#sha256:abc"})["Item"]["title"]
        == "Book_A"
    )


def test_a_renamed_re_upload_is_sent_to_review(th, aws):
    s3, _ = aws
    for key in ("contentrepository/A/A.pdf", "contentrepository/B/B.pdf"):
        s3.put_object(Bucket="buck", Key=key, Body=b"%PDF the same bytes")
    with patch("src.ingestion.review_reject.reject_to_review") as rej:
        assert th._source_renamed("contentrepository/A/A.pdf", "A") is False
        assert th._source_renamed("contentrepository/B/B.pdf", "B") is True
    assert rej.call_args.kwargs["category"] == "source-renamed"
    assert "'A'" in rej.call_args.args[4]


def test_a_different_document_is_its_own_book(th, aws):
    s3, _ = aws
    s3.put_object(Bucket="buck", Key="contentrepository/A/A.pdf", Body=b"one")
    s3.put_object(Bucket="buck", Key="contentrepository/B/B.pdf", Body=b"two")
    with patch("src.ingestion.review_reject.reject_to_review") as rej:
        assert th._source_renamed("contentrepository/A/A.pdf", "A") is False
        assert th._source_renamed("contentrepository/B/B.pdf", "B") is False
    rej.assert_not_called()


def test_a_file_over_the_limit_is_not_checksummed(th, aws, monkeypatch):
    s3, table = aws
    s3.put_object(Bucket="buck", Key="contentrepository/A/A.pdf", Body=b"x" * 10)
    monkeypatch.setattr(th, "_SOURCE_BIND_MAX_BYTES", 5)
    assert th._source_renamed("contentrepository/A/A.pdf", "A") is False
    assert table.scan()["Count"] == 0


def test_the_binding_fails_open_and_loud(th, monkeypatch):
    broken = MagicMock()
    broken.put_item.side_effect = RuntimeError("ddb down")
    broken.meta.client.exceptions.ConditionalCheckFailedException = KeyError
    monkeypatch.setattr(th, "dynamo", broken)
    with patch.object(th, "observe_error") as err:
        assert th._bind_source("sha256:abc", "A") == ""
    assert err.call_args.args[:2] == ("intake", "source_bind_failed")


# --- unnamed uploads / unknown --------------------------------------------------------------------


def test_an_unnamed_upload_is_sent_to_review(th):
    with (
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review") as rej,
    ):
        assert (
            th._unnamed_upload("contentrepository/x/Bad Name!.pdf", "unknown") is True
        )
        assert th._unnamed_upload("contentrepository/A/A.pdf", "A") is False
    rej.assert_called_once()
    assert rej.call_args.kwargs["category"] == "unnamed-upload"


@pytest.mark.parametrize("name", ["unknown", "..", "a/b"])
def test_no_task_is_launched_for_an_unknown_or_unsafe_book(th, name):
    with (
        patch.object(th, "observe_error") as err,
        patch.object(th, "_run_av_scan") as av,
        patch.object(th.boto3, "client") as client,
    ):
        assert th._run_task("phase2:1", "s3", book_name=name) is None
    assert err.call_args.args[:2] == ("trigger", "book_name_rejected")
    av.assert_not_called()
    client.assert_not_called()


# --- Phase 1 ---------------------------------------------------------------------------------------


def test_phase1_overrules_a_chapter_meta_naming_another_book():
    import phase1_parse as p1

    doc = SimpleNamespace(book="Cross-Channel Attack", chapter_number="8")
    with patch("src.utils.observe.observe_error") as err:
        p1._enforce_book_slug(doc, "USA-E-XChannel")
    assert doc.book == "USA-E-XChannel"
    assert err.call_args.args[:2] == ("phase1", "book_slug_mismatch")
    assert err.call_args.kwargs["meta_book"] == "Cross-Channel Attack"


def test_phase1_leaves_a_matching_book_alone():
    import phase1_parse as p1

    doc = SimpleNamespace(book="USA-E-XChannel", chapter_number="8")
    with patch("src.utils.observe.observe_error") as err:
        p1._enforce_book_slug(doc, "USA-E-XChannel")
    err.assert_not_called()


def test_the_chapter_meta_carries_the_book_id():
    import yaml

    from src.ingestion.chapter_meta import build_meta_yaml

    meta = yaml.safe_load(
        build_meta_yaml(
            "USA-E-XChannel",
            source_url="https://example.org/x",
            source_url_basis="test",
            source_key="contentrepository/USA-E-XChannel/USA-E-XChannel.pdf",
        )
    )
    assert meta["book"] == "USA-E-XChannel"
    assert meta["book_id"] == "57SJMAQBZYEWDEHACZVT8EZ7AP"
