"""Phase-3 URL-review completion notification: fires only when pending>0, fail-safe."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
import ecs_entrypoint as E


def test_no_topic_no_publish(monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    # must simply return without error
    E._notify_url_review_pending()


def test_zero_pending_no_publish(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setattr("src.enrichment.fetch_review.list_pending", lambda: [])
    published = {}
    monkeypatch.setattr(E, "boto3", _fake_boto3(published))
    E._notify_url_review_pending()
    assert "msg" not in published  # nothing published when zero pending


def test_pending_publishes_with_link(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setenv("URL_REVIEW_URL", "https://api/app/urlreview")
    monkeypatch.setattr(
        "src.enrichment.fetch_review.list_pending",
        lambda: [{"fetch_event_id": "e1"}, {"fetch_event_id": "e2"}],
    )
    published = {}
    monkeypatch.setattr(E, "boto3", _fake_boto3(published))
    E._notify_url_review_pending()
    assert "2 URL(s)" in published["subject"]
    assert "https://api/app/urlreview" in published["msg"]


def test_failsafe_on_error(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setattr(
        "src.enrichment.fetch_review.list_pending",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # must not raise
    E._notify_url_review_pending()


def _fake_boto3(published):
    class _C:
        def publish(self, TopicArn, Subject, Message):
            published["subject"] = Subject
            published["msg"] = Message

    class _B:
        @staticmethod
        def client(*a, **k):
            return _C()

    return _B()
