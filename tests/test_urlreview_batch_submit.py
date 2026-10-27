"""Batched Phase-1 submission of affirmed URL-review finds (one infra spin-up per batch)."""

import json
import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
import src.enrichment.fetch_review as FR
from src.utils import fetch_provenance as fp


class _S3:
    def __init__(self):
        self.puts = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append(
            {"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType}
        )


def test_submit_batch_uploads_all_and_marks_submitted(monkeypatch):
    affirmed = [
        {
            "fetch_event_id": "e1",
            "endpoint": "https://a.example/x",
            "provider": "wayback",
        },
        {"fetch_event_id": "e2", "endpoint": "https://b.example/y", "provider": "nara"},
    ]
    monkeypatch.setattr(FR, "list_affirmed", lambda: affirmed)
    s3 = _S3()
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (s3, "test-bucket"))
    marked = []
    monkeypatch.setattr(
        FR,
        "set_review_status",
        lambda fid, status: marked.append((fid, status)) or True,
    )

    out = FR.submit_affirmed_batch()
    assert out["submitted"] == 2 and out["skipped"] == 0
    assert out["source_ids"] == ["fetchfind-e1", "fetchfind-e2"]
    # all uploaded under the batched contentrepository/ front door, as .html (-> parse_keys)
    assert all(
        p["Key"].startswith("contentrepository/_system/") and p["Key"].endswith(".html")
        for p in s3.puts
    )
    assert len(s3.puts) == 2
    # each marked SUBMITTED (so never re-submitted)
    assert marked == [("e1", fp.SUBMITTED), ("e2", fp.SUBMITTED)]


def test_submit_batch_empty_is_noop(monkeypatch):
    monkeypatch.setattr(FR, "list_affirmed", lambda: [])
    called = {}
    monkeypatch.setattr(
        FR, "_s3_client_and_bucket", lambda: (called.setdefault("c", True), "b")
    )
    out = FR.submit_affirmed_batch()
    assert out == {"submitted": 0, "source_ids": [], "skipped": 0, "deduped": 0}
    assert "c" not in called  # never even resolves S3 on an empty batch


def test_submit_batch_no_bucket_skips(monkeypatch):
    monkeypatch.setattr(
        FR, "list_affirmed", lambda: [{"fetch_event_id": "e1", "endpoint": "http://x"}]
    )
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (None, ""))
    out = FR.submit_affirmed_batch()
    assert out["submitted"] == 0 and out["skipped"] == 1


def test_one_bad_find_does_not_sink_batch(monkeypatch):
    affirmed = [{"fetch_event_id": "ok"}, {"fetch_event_id": "bad"}]
    monkeypatch.setattr(FR, "list_affirmed", lambda: affirmed)

    class _S3bad:
        def put_object(self, Bucket, Key, Body, ContentType):
            if "bad" in Key:
                raise RuntimeError("s3 down")

    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (_S3bad(), "b"))
    monkeypatch.setattr(FR, "set_review_status", lambda fid, status: True)
    out = FR.submit_affirmed_batch()
    assert (
        out["submitted"] == 1
        and out["skipped"] == 1
        and out["source_ids"] == ["fetchfind-ok"]
    )


def test_safe_token_sanitizes():
    assert FR._safe_token("a b/c?d", "fb") == "a-b-c-d"
    assert FR._safe_token("", "fb") == "fb"
    assert FR._safe_token("///", "fb") == "fb"


def test_handler_submit_route(monkeypatch):
    import lambda_handlers.urlreview_ui_handler as H

    monkeypatch.setattr(
        "src.enrichment.fetch_review.submit_affirmed_batch",
        lambda: {"submitted": 3, "source_ids": ["a", "b", "c"], "skipped": 0},
    )
    r = H.handler({"httpMethod": "POST", "path": "/urlreview/api/submit"}, None)
    d = json.loads(r["body"])
    assert r["statusCode"] == 200 and d["submitted"] == 3


def test_handler_affirmed_count_route(monkeypatch):
    import lambda_handlers.urlreview_ui_handler as H

    monkeypatch.setattr(
        "src.enrichment.fetch_review.list_affirmed", lambda: [{"fetch_event_id": "e1"}]
    )
    r = H.handler({"httpMethod": "GET", "path": "/urlreview/api/affirmed"}, None)
    assert json.loads(r["body"])["count"] == 1


def test_unconfirmed_status_write_counts_as_skip(monkeypatch):
    """If the SUBMITTED status write isn't confirmed, the find is NOT counted submitted
    (stays AFFIRMED for the next batch) even though the upload succeeded."""
    monkeypatch.setattr(
        FR, "list_affirmed", lambda: [{"fetch_event_id": "e1", "endpoint": "http://x"}]
    )
    s3 = _S3()
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (s3, "b"))
    monkeypatch.setattr(
        FR, "set_review_status", lambda fid, status: False
    )  # write not confirmed
    out = FR.submit_affirmed_batch()
    assert out["submitted"] == 0 and out["skipped"] == 1 and out["source_ids"] == []
    assert (
        len(s3.puts) == 1
    )  # content WAS uploaded (deterministic key -> safe re-click overwrite)


def test_duplicate_content_is_deduped_not_reuploaded(monkeypatch):
    """GLOBAL content-hash dedup: identical bytes already submitted -> skip upload, mark SUBMITTED,
    count as deduped (not submitted)."""
    monkeypatch.setattr(
        FR,
        "list_affirmed",
        lambda: [{"fetch_event_id": "dup1", "endpoint": "http://a.example/x"}],
    )
    s3 = _S3()
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (s3, "b"))
    monkeypatch.setattr(
        FR, "_register_content_hash_if_new", lambda digest, sid: False
    )  # already seen
    marked = []
    monkeypatch.setattr(
        FR, "set_review_status", lambda fid, st: marked.append((fid, st)) or True
    )
    out = FR.submit_affirmed_batch()
    assert out["deduped"] == 1 and out["submitted"] == 0
    assert len(s3.puts) == 0  # NOT re-uploaded
    from src.utils import fetch_provenance as fp

    assert marked == [("dup1", fp.SUBMITTED)]  # terminal: known duplicate


def test_new_content_proceeds(monkeypatch):
    monkeypatch.setattr(
        FR,
        "list_affirmed",
        lambda: [{"fetch_event_id": "new1", "endpoint": "http://a.example/x"}],
    )
    s3 = _S3()
    monkeypatch.setattr(FR, "_s3_client_and_bucket", lambda: (s3, "b"))
    monkeypatch.setattr(
        FR, "_register_content_hash_if_new", lambda digest, sid: True
    )  # new
    monkeypatch.setattr(FR, "set_review_status", lambda fid, st: True)
    out = FR.submit_affirmed_batch()
    assert out["submitted"] == 1 and out["deduped"] == 0 and len(s3.puts) == 1


def test_content_hash_failopen_without_table(monkeypatch):
    """No table (local/test) -> _register_content_hash_if_new fail-OPENS (True) so dedup never
    blocks a real submit."""
    from src.utils import fetch_provenance as fp

    monkeypatch.setattr(FR, "_resolve_table", lambda _fp: None)
    assert FR._register_content_hash_if_new("sha256:abc", "fetchfind-x") is True


def test_content_hash_duplicate_on_conditional_failure(monkeypatch):
    """A ConditionalCheckFailed on the claim put -> content already registered -> returns False."""

    class _T:
        def put_item(self, **k):
            raise RuntimeError("ConditionalCheckFailedException: exists")

    monkeypatch.setattr(FR, "_resolve_table", lambda _fp: _T())
    assert FR._register_content_hash_if_new("sha256:abc", "fetchfind-x") is False


def test_content_hash_new_on_successful_put(monkeypatch):
    class _T:
        def put_item(self, **k):
            return {}

    monkeypatch.setattr(FR, "_resolve_table", lambda _fp: _T())
    assert FR._register_content_hash_if_new("sha256:abc", "fetchfind-x") is True


def test_content_hash_duplicate_via_botocore_error_code(monkeypatch):
    """A botocore-style ClientError (response['Error']['Code']=ConditionalCheckFailedException)
    is classified as a duplicate (False)."""

    class _Err(Exception):
        def __init__(self):
            self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}

    class _T:
        def put_item(self, **k):
            raise _Err()

    monkeypatch.setattr(FR, "_resolve_table", lambda _fp: _T())
    assert FR._register_content_hash_if_new("sha256:abc", "fetchfind-x") is False


def test_content_hash_transient_error_failopen(monkeypatch):
    """A non-conditional (transient) error -> fail-OPEN (True), never blocks a real submit."""

    class _Err(Exception):
        def __init__(self):
            self.response = {
                "Error": {"Code": "ProvisionedThroughputExceededException"}
            }

    class _T:
        def put_item(self, **k):
            raise _Err()

    monkeypatch.setattr(FR, "_resolve_table", lambda _fp: _T())
    assert FR._register_content_hash_if_new("sha256:abc", "fetchfind-x") is True
