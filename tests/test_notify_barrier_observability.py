"""Dedup-notify delivery observability + barrier-timeout outstanding-batch detection (E2E findings)."""

import os
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as E


class _SNS:
    def __init__(self, subs):
        self._subs = subs

    def list_subscriptions_by_topic(self, TopicArn):
        return {"Subscriptions": self._subs}


def test_topic_has_confirmed_sub_true():
    sns = _SNS([{"SubscriptionArn": "arn:aws:sns:...:abc123"}])
    assert E._topic_has_confirmed_sub(sns, "arn:topic") is True


def test_topic_has_confirmed_sub_false_pending_only():
    sns = _SNS([{"SubscriptionArn": "PendingConfirmation"}])
    assert E._topic_has_confirmed_sub(sns, "arn:topic") is False


def test_topic_has_confirmed_sub_false_empty():
    assert E._topic_has_confirmed_sub(_SNS([]), "arn:topic") is False


def test_topic_has_confirmed_sub_failsafe_true_on_error():
    class Boom:
        def list_subscriptions_by_topic(self, TopicArn):
            raise RuntimeError("no perms")
    # can't check -> must NOT claim undelivered
    assert E._topic_has_confirmed_sub(Boom(), "arn:topic") is True


def test_pending_batches_for_book_failsafe(monkeypatch):
    # job_queue lookup error -> 0, never raises
    import src.utils.job_queue as jq
    monkeypatch.setattr(jq, "get_active_jobs", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert E._pending_batches_for_book() == 0


def test_pending_batches_for_book_filters_by_book(monkeypatch):
    import src.utils.job_queue as jq
    from types import SimpleNamespace
    monkeypatch.setenv("BOOK_NAME", "XChannel")
    monkeypatch.setattr(jq, "get_active_jobs", lambda: [
        SimpleNamespace(book="XChannel"), SimpleNamespace(book="OtherBook"),
        SimpleNamespace(book="XChannel"),
    ])
    assert E._pending_batches_for_book() == 2
