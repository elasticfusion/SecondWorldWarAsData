"""Tests for the failure-cause taxonomy (M7, spec §6.1)."""

from src.utils.failure_taxonomy import Cause, Policy, classify


def test_funding_402_credit():
    c = classify(status=402, body="insufficient credit")
    assert c.cause == Cause.FUNDING
    assert c.policy == Policy.HOLD
    assert c.retryable is False  # must NOT retry funding as transient


def test_rate_limit_429():
    c = classify(status=429, body="rate limited")
    assert c.cause == Cause.RATE_LIMIT
    assert c.policy == Policy.RETRY_PACED
    assert c.retryable is True


def test_model_not_found_is_not_retryable():
    c = classify(status=404, body="The model grok-old was not found")
    assert c.cause == Cause.MODEL_TRANSITION
    assert c.policy == Policy.FALLBACK_MODEL
    assert c.retryable is False  # retrying a dead model is useless


def test_served_model_mismatch_is_model_transition():
    c = classify(served_model="grok-4.7", requested_model="grok-4.20-0309-reasoning")
    assert c.cause == Cause.MODEL_TRANSITION


def test_size_limit_split():
    c = classify(status=413, body="request too large, exceeds limit")
    assert c.cause == Cause.SIZE_LIMIT
    assert c.policy == Policy.SPLIT_RESUBMIT
    assert c.retryable is False  # transform, not plain retry


def test_finish_reason_length_is_size_limit():
    c = classify(finish_reason="length")
    assert c.cause == Cause.SIZE_LIMIT


def test_content_filter_needs_review():
    c = classify(finish_reason="content_filter")
    assert c.cause == Cause.CONTENT_POLICY
    assert c.policy == Policy.NEEDS_REVIEW
    assert c.retryable is False


def test_transient_5xx():
    c = classify(status=503, body="service unavailable")
    assert c.cause == Cause.TRANSIENT
    assert c.policy == Policy.RETRY
    assert c.retryable is True


def test_batch_all_errors_is_batch_failed():
    c = classify(num_requests=10, num_error=10, num_success=0)
    assert c.cause == Cause.BATCH_FAILED
    assert c.policy == Policy.RESUBMIT_BOUNDED


def test_partial_batch():
    c = classify(num_requests=10, num_error=3, num_success=7)
    assert c.cause == Cause.PARTIAL
    assert c.policy == Policy.RECOVER_SUBSET


def test_exception_timeout_is_transient():
    c = classify(exception=TimeoutError("timed out"))
    assert c.cause == Cause.TRANSIENT


def test_exception_jsondecode_is_poison():
    c = classify(exception=ValueError("Expecting value: JSON decode"))
    assert c.cause == Cause.POISON
    assert c.retryable is False


def test_unknown_defaults_to_needs_review_not_retry():
    """Unclassifiable => needs-review, NOT a blind retry loop."""
    c = classify(status=418, body="teapot")
    assert c.cause == Cause.UNKNOWN
    assert c.retryable is False


def test_error_code_takes_priority():
    c = classify(status=500, error_code="insufficient_credit")
    assert c.cause == Cause.FUNDING  # code beats the 5xx-looks-transient status


def test_permanent_causes_never_retryable():
    """Invariant: model-transition, funding, size, content, poison, unknown
    must all be non-retryable (never fed to a plain retry loop)."""
    for kw in (
        {"status": 404, "body": "model not found"},
        {"status": 402, "body": "quota exceeded"},
        {"status": 413, "body": "exceeds limit"},
        {"finish_reason": "content_filter"},
        {"exception": ValueError("json decode")},
        {"status": 418, "body": "teapot"},
    ):
        assert classify(**kw).retryable is False
