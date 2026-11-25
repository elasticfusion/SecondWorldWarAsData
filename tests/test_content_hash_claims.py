"""Task 1.6: the global content-hash dedup claim (contenthash#<sha256>) is given back when the
content was NOT stored - a failed upload or write used to leave the claim, so every later run
skipped the content as a duplicate of something that never landed."""

import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from src.enrichment import fetch_review as fr  # noqa: E402


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        t = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="claims",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(fr, "_resolve_table", lambda fp: t)
        yield t


def _claimed(table, digest):
    return table.get_item(Key={"cache_key": f"contenthash#{digest}"}).get("Item")


def test_a_claim_is_released_only_by_its_own_writer(table):
    assert fr._register_content_hash_if_new("sha256:aa", "writer-1")
    fr.release_content_hash("sha256:aa", "writer-2")  # not ours: kept
    assert _claimed(table, "sha256:aa")
    fr.release_content_hash("sha256:aa", "writer-1")
    assert _claimed(table, "sha256:aa") is None
    assert fr._register_content_hash_if_new(
        "sha256:aa", "writer-1"
    )  # can be claimed again


def test_a_block_that_raises_gives_the_claim_back(table):
    with pytest.raises(RuntimeError):
        with fr.claimed_content("sha256:bb", "w") as new:
            assert new
            raise RuntimeError("S3 PutObject failed")
    assert _claimed(table, "sha256:bb") is None


def test_a_duplicate_is_not_released(table):
    assert fr._register_content_hash_if_new("sha256:cc", "first")
    with pytest.raises(RuntimeError):
        with fr.claimed_content("sha256:cc", "second") as new:
            assert not new
            raise RuntimeError("x")
    assert _claimed(table, "sha256:cc")  # the first writer's claim is untouched


def test_a_release_that_fails_is_alerted(monkeypatch):
    t = mock.Mock()
    t.delete_item.side_effect = RuntimeError("throttled")
    monkeypatch.setattr(fr, "_resolve_table", lambda fp: t)
    sent = []
    import src.utils.observe as obs

    monkeypatch.setattr(obs, "observe_error", lambda s, e, **k: sent.append((s, e, k)))
    fr.release_content_hash("sha256:dd", "w")
    [(scope, event, fields)] = sent
    assert (scope, event) == ("content_hash", "release_failed")
    assert "contenthash#sha256:dd" in fields["cache_key"]


def test_a_find_whose_upload_fails_is_not_lost(table, monkeypatch):
    monkeypatch.setattr(fr, "_find_content_bytes", lambda find: b"<html>doc</html>")
    statuses = []
    monkeypatch.setattr(
        fr, "set_review_status", lambda fid, st: statuses.append(st) or True
    )
    client = mock.Mock()
    client.put_object.side_effect = RuntimeError("S3 down")
    result = {"submitted": 0, "deduped": 0, "skipped": 0, "source_ids": []}
    fr._submit_one_find({"fetch_event_id": "f1"}, client, "bucket", result)
    assert (
        result["skipped"] == 1 and statuses == []
    )  # still AFFIRMED: retried next batch
    digest = fr._content_sha256(b"<html>doc</html>")
    assert _claimed(table, digest) is None  # and not blocked as a "duplicate"
    client.put_object.side_effect = None
    fr._submit_one_find({"fetch_event_id": "f1"}, client, "bucket", result)
    assert result["submitted"] == 1


def test_a_claim_that_errored_is_never_released(monkeypatch):
    """Fail-open: nothing was claimed, so a failed write must not delete a claim an earlier
    run holds for the same (deterministic) source id."""
    t = mock.Mock()
    t.put_item.side_effect = RuntimeError("throttled")
    monkeypatch.setattr(fr, "_resolve_table", lambda fp: t)
    with pytest.raises(RuntimeError):
        with fr.claimed_content("sha256:ee", "w") as new:
            assert new  # proceed: dedup never blocks a real submit
            raise RuntimeError("S3 down")
    t.delete_item.assert_not_called()
