"""Failure-cause taxonomy + per-cause recovery policy (M7 / CONCURRENCY_AND_NAT_SPEC §6.1).

The batch layer today classifies by response SHAPE (valid/truncated/empty/error)
— *what it looked like* — not by CAUSE / retryability. M7 adds the missing
abstraction: classify each failure (HTTP status, xAI error code, finish_reason,
served-model mismatch) into a CAUSE, then route to that cause's POLICY. Any new
failure cause slots in without new special-casing; model-transition is just the
'config/transitional' row.

Design rules (§6.1):
  transient  -> retry (backoff)
  config     -> do NOT retry blindly; apply fallback model then resubmit
  funding    -> HOLD unsubmitted work (don't drop), alert, resume on top-up
  structural -> transform (split/chunk) then resubmit
  permanent  -> stop; route to needs-review (never retry)
  mixed      -> recover the failed subset only
  batch_level-> bounded resubmit-on-failure -> needs-review
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Cause(str, Enum):
    TRANSIENT = "transient"  # 5xx / connection / timeout / 429
    RATE_LIMIT = "rate_limit"  # 429 specifically (paced retry)
    MODEL_TRANSITION = "model_transition"  # retirement / redirect / not-found
    FUNDING = "funding"  # credit / quota exhausted (402/403)
    SIZE_LIMIT = "size_limit"  # batch/request exceeds a hard limit
    CONTENT_POLICY = "content_policy"  # content_filter / policy refusal
    POISON = "poison"  # malformed / unparseable content
    PARTIAL = "partial"  # some requests ok, some failed
    BATCH_FAILED = "batch_failed"  # whole batch failed / 24h expiry
    UNKNOWN = "unknown"


class Policy(str, Enum):
    RETRY = "retry"  # transient: retry same, backoff
    RETRY_PACED = "retry_paced"  # honor Retry-After / cluster budget
    FALLBACK_MODEL = "fallback_model"  # switch model, then resubmit
    HOLD = "hold"  # do NOT drop; pause + alert; resume on top-up
    SPLIT_RESUBMIT = "split_resubmit"  # chunk into sub-batches
    NEEDS_REVIEW = "needs_review"  # stop; human queue (never retry)
    RECOVER_SUBSET = "recover_subset"  # retry only the failed requests
    RESUBMIT_BOUNDED = "resubmit_bounded"  # bounded whole-batch resubmit


@dataclass(frozen=True)
class Classification:
    cause: Cause
    policy: Policy
    retryable: bool
    reason: str


# Per-cause policy table (§6.1). retryable=False => a retry loop must NOT fire.
_POLICY: dict = {
    Cause.TRANSIENT: (Policy.RETRY, True),
    Cause.RATE_LIMIT: (Policy.RETRY_PACED, True),
    Cause.MODEL_TRANSITION: (Policy.FALLBACK_MODEL, False),  # don't retry a dead model
    Cause.FUNDING: (Policy.HOLD, False),  # hold work, don't retry-as-transient
    Cause.SIZE_LIMIT: (Policy.SPLIT_RESUBMIT, False),  # transform, not plain retry
    Cause.CONTENT_POLICY: (Policy.NEEDS_REVIEW, False),
    Cause.POISON: (Policy.NEEDS_REVIEW, False),
    Cause.PARTIAL: (Policy.RECOVER_SUBSET, True),
    Cause.BATCH_FAILED: (Policy.RESUBMIT_BOUNDED, True),
    Cause.UNKNOWN: (Policy.NEEDS_REVIEW, False),  # safe default: don't loop blindly
}


def _is_model_not_found(body: str) -> bool:
    """True if the body indicates a retired/redirected/not-found model."""
    return "model" in body and any(
        k in body for k in ("not found", "not_found", "deprecat", "retired")
    )


def _classify_http(status: int, body: str) -> Optional[Cause]:
    """Map an HTTP status (+ body hint) to a cause via a rule table."""
    b = (body or "").lower()
    rules = (
        ((402, 403), ("credit", "quota", "insufficient"), Cause.FUNDING),
        ((429,), (), Cause.RATE_LIMIT),
        ((400, 413), ("too large", "exceed", "limit", "too many"), Cause.SIZE_LIMIT),
    )
    if status in (400, 404) and _is_model_not_found(b):
        return Cause.MODEL_TRANSITION
    for statuses, keywords, cause in rules:
        if status in statuses and (not keywords or any(k in b for k in keywords)):
            return cause
    if status >= 500 or status == 408:
        return Cause.TRANSIENT
    return None


_ERROR_CODE_KEYWORDS = (
    (("credit", "quota", "billing"), Cause.FUNDING),
    (
        ("not_found", "deprecat", "retired"),
        Cause.MODEL_TRANSITION,
    ),  # gated on 'model' below
    (("rate", "429"), Cause.RATE_LIMIT),
    (("too_large", "size", "limit"), Cause.SIZE_LIMIT),
    (("content_filter", "policy"), Cause.CONTENT_POLICY),
)


def _from_error_code(error_code: str = "", **_) -> Optional[Cause]:
    ec = (error_code or "").lower()
    if not ec:
        return None
    for keywords, cause in _ERROR_CODE_KEYWORDS:
        if cause is Cause.MODEL_TRANSITION and "model" not in ec:
            continue  # model-transition keywords only count with 'model'
        if any(k in ec for k in keywords):
            return cause
    return None


def _from_http(status: Optional[int] = None, body: str = "", **_) -> Optional[Cause]:
    if status is None:
        return None
    return _classify_http(status, body)


def _from_finish_reason(finish_reason: str = "", **_) -> Optional[Cause]:
    fr = (finish_reason or "").lower()
    if fr == "content_filter":
        return Cause.CONTENT_POLICY
    if fr == "length":
        return Cause.SIZE_LIMIT  # truncated by max_tokens -> split/chunk
    return None


def _from_model_mismatch(
    served_model: str = "", requested_model: str = "", **_
) -> Optional[Cause]:
    if served_model and requested_model and served_model != requested_model:
        return Cause.MODEL_TRANSITION  # silent redirect to a different model
    return None


def _from_batch_counts(
    num_requests: Optional[int] = None,
    num_error: Optional[int] = None,
    num_success: Optional[int] = None,
    **_,
) -> Optional[Cause]:
    if num_requests is None or num_requests <= 0:
        return None
    err = num_error or 0
    ok = num_success or 0
    if err >= num_requests:
        return Cause.BATCH_FAILED
    if err > 0 and ok > 0:
        return Cause.PARTIAL
    return None


def _from_exception(exception: Optional[BaseException] = None, **_) -> Optional[Cause]:
    if exception is None:
        return None
    name = type(exception).__name__.lower()
    if "timeout" in name or "connection" in name:
        return Cause.TRANSIENT
    if "json" in name or "decode" in name or "value" in name:
        return Cause.POISON
    return None


# Priority order: explicit error code > HTTP > finish_reason > model mismatch >
# batch counts > exception type. First match wins.
_CLASSIFIERS = (
    _from_error_code,
    _from_http,
    _from_finish_reason,
    _from_model_mismatch,
    _from_batch_counts,
    _from_exception,
)


def classify(**signals) -> Classification:
    """Classify a batch/request failure into (cause, policy, retryable).

    Runs the priority-ordered single-signal classifiers; first match wins.
    Defaults to UNKNOWN -> needs-review (never loop blindly). Accepts: status,
    body, error_code, finish_reason, served_model, requested_model, exception,
    num_success, num_error, num_requests.
    """
    cause: Optional[Cause] = None
    for fn in _CLASSIFIERS:
        cause = fn(**signals)
        if cause is not None:
            break
    if cause is None:
        cause = Cause.UNKNOWN
    policy, retryable = _POLICY[cause]
    return Classification(
        cause=cause, policy=policy, retryable=retryable, reason=cause.value
    )
