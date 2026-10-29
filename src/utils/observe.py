"""The generic observability "out" — a single fail-safe sink every anomaly/failure routes through.

WHY THIS EXISTS
---------------
The 2026-10-29 XChannel-8 incident (groups=0 fail_stop) exposed PATTERN-8: a failure path that
returns an empty/None/skip sentinel WITHOUT a canonical, greppable anomaly at the point of loss, so
the data-loss was (a) indistinguishable from a legitimate zero and (b) only caught by the end-of-phase
yield gate 2.5h later. The codebase already had an `OBSERV <scope>.<event>` *convention* and ~15
hand-rolled `_observe_*` helpers, but NO single primitive — so shape drifted and the pattern re-seeded.

This module is the missing primitive. Two design requirements (from the owner):
  1. It must NOT stop progress — `observe()` is a SINK, not a gate. It never raises into the caller;
     an observability failure can never break a phase. (A separate end-of-phase gate may still CHOOSE
     to fail_stop on aggregate anomalies — that is orthogonal.)
  2. It must be QUERYABLE — every call emits one stable, greppable line with a fixed grammar:

        OBSERV <scope>.<event> status=<ok|warn|error> [key=value ...]

     Plain-text `%(message)s` logs (this repo's format) become queryable via:
       - grep/ripgrep:            rg 'OBSERV [A-Za-z_]+[.][A-Za-z_]+ status=error'
       - CloudWatch Logs Insights: filter @message like /OBSERV .* status=error/
                                    | parse @message "OBSERV * status=*" as scope, status
                                    | stats count() by scope, status

GRAMMAR (stable — do not reorder the leading tokens)
----------------------------------------------------
  OBSERV <scope>.<event> status=<status> [produced=N] [reason=<token>] [k=v ...] [— human note]

  scope   : the subsystem/entity (e.g. "groups", "grok", "yield", "dedup", "phase2").
  event   : the specific thing observed (e.g. "batch_error", "json_failed", "extract").
  status  : "ok" (healthy), "warn" (degraded/recoverable), "error" (data-affecting failure).
  produced: for extraction paths, how many entities actually came out (so a FAILURE zero is
            distinguishable from a LEGITIMATE zero — the core PATTERN-8 defense).

USAGE
-----
    from src.utils.observe import observe, observe_error

    # a dropped extraction batch (the batch_parallel.py:715 class):
    observe_error("groups", "batch_error", reason=type(e).__name__, produced=0, event_id=event_id)

    # a healthy rollup:
    observe("groups", "extract", status="ok", produced=74, event_id=event_id)

    # a degraded fallback that still made progress:
    observe("places", "enrich_error", status="warn", reason="wiki_timeout", name=place_name)

The emission is wrapped so it can NEVER raise into the phase (fail-safe). If logging itself fails,
observe() falls back to best-effort stderr and finally swallows — because an observer that breaks the
pipeline is worse than a missed log line.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger("observe")

# The three canonical statuses, mapped to log levels. "error" is a data-affecting failure (loud,
# alertable); "warn" is a degraded-but-recovered path; "ok" is a healthy rollup (INFO).
_STATUS_LEVEL = {
    "ok": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

# ---- Slack escalation ---------------------------------------------------------------------------
# status=error anomalies ALSO go to Slack, via the pipeline's existing notify chain:
#     observe(status=error) -> SNS NOTIFICATION_TOPIC_ARN -> slack_formatter Lambda -> Slack
# This is OPT-IN and fail-safe: if NOTIFICATION_TOPIC_ARN is unset (local/test/CI), nothing is sent
# and observe() is log-only. A publish failure is swallowed (Slack can never stop the pipeline).
#
# Rate-limit: Slack is for OPERATOR ATTENTION, not a firehose. The same (scope.event) key is
# escalated at most once per _SLACK_DEDUP_WINDOW_S so a tight failure loop (e.g. every sub-event in
# a book dropping) produces ONE Slack ping, not thousands. The full detail still lands in the logs
# (the queryable "out"); Slack is the attention signal. Set OBSERVE_SLACK=0 to disable entirely.
_SLACK_DEDUP_WINDOW_S = float(os.getenv("OBSERVE_SLACK_DEDUP_S", "300"))
_slack_last_sent: dict[str, float] = {}


def _slack_enabled() -> bool:
    return os.getenv("OBSERVE_SLACK", "1") not in ("0", "false", "False") and bool(
        os.environ.get("NOTIFICATION_TOPIC_ARN")
    )


def _should_escalate(key: str, now: float) -> bool:
    """True at most once per dedup window per (scope.event) key — fail-safe, O(1)."""
    last = _slack_last_sent.get(key, 0.0)
    if now - last >= _SLACK_DEDUP_WINDOW_S:
        _slack_last_sent[key] = now
        return True
    return False


def _escalate_to_slack(scope: str, event: str, line: str) -> None:
    """Publish a status=error OBSERV line to the notify SNS topic (-> formatter -> Slack).

    Fail-safe and best-effort: unset topic => no-op; any boto3/publish error is swallowed. Never
    raises. Rate-limited per (scope.event) so a failure loop pings Slack once, not thousands of times.
    """
    try:
        if not _slack_enabled():
            return
        if not _should_escalate(f"{scope}.{event}", time.time()):
            return
        import boto3  # lazy: keep boto3 out of the hot path / local imports

        topic = os.environ["NOTIFICATION_TOPIC_ARN"]
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic,
            Subject=f"WWII Pipeline: OBSERV error — {scope}.{event}"[:100],
            Message=line,
        )
    except Exception:  # noqa: BLE001 - Slack escalation must NEVER stop the pipeline
        logger.debug("observe slack-escalation failed (swallowed)", exc_info=True)


def _safe(value: Any, limit: int = 200) -> str:
    """Coerce a value to a single greppable, injection-safe token.

    Collapses ALL whitespace — including newlines, carriage returns, tabs, form/vertical feeds AND
    the Unicode line separators U+2028/U+2029/U+0085 (which some log viewers/CloudWatch treat as
    line breaks) — to '_', and bounds the length. This prevents a caller-influenced value from
    FORGING a second `OBSERV ... status=ok` line that would mask a real failure (log-integrity; the
    exact PATTERN-8 masking the sink exists to prevent). Applied to scope, event, note, and every
    field value — nothing caller-controlled reaches the line un-sanitized.
    """
    token = str(value)
    # str.split() already handles \t\n\r\f\v and ASCII spaces; add the Unicode line separators.
    for sep in ("\u2028", "\u2029", "\u0085"):
        token = token.replace(sep, " ")
    token = "_".join(token.split())
    if len(token) > limit:
        token = token[: limit - 3] + "..."
    return token


def _format_kv(fields: dict[str, Any]) -> str:
    """Render extra fields as ` key=value` tokens, each value sanitized to a single grep-safe token
    (no internal whitespace, bounded length) via `_safe` so the grammar stays parseable and a field
    value cannot inject a newline / forge a second OBSERV line."""
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{_safe(key, 80)}={_safe(value)}")
    return (" " + " ".join(parts)) if parts else ""


def observe(
    scope: str,
    event: str,
    *,
    status: str = "ok",
    note: str = "",
    **fields: Any,
) -> str:
    """The generic observability sink. Emit one stable `OBSERV <scope>.<event> status=...` line.

    This is the single "out" every anomaly/failure/degraded-fallback/rollup should route through.
    It NEVER raises (fail-safe): an observability failure must not stop pipeline progress. It is NOT
    a gate — it only records; callers continue regardless of what they pass here.

    SECURITY: scope, event, note, and all field keys/values are sanitized (`_safe`) so caller- or
    model-derived text cannot inject a newline and forge a second OBSERV line (log integrity). Do
    NOT put secrets or PII in `fields`/`note`: on `status=error` the full line is forwarded
    off-platform to Slack via the SNS escalation. Reference entities by id/name only.

    Args:
        scope:  subsystem/entity (e.g. "groups", "grok", "dedup", "phase2").
        event:  the specific observation (e.g. "batch_error", "json_failed", "extract").
        status: "ok" | "warn" | "error" (anything else is coerced to "error" — fail loud).
        note:   optional human-readable trailing clause (appended after " — "). Sanitized.
        **fields: extra key=value context (e.g. produced=0, reason="Extra_data", event_id=...).
                  `None`-valued fields are omitted. Put `produced=` on extraction paths so a
                  failure-zero is distinguishable from a legitimate zero (the PATTERN-8 defense).
                  Must NOT contain secrets/PII (forwarded to Slack on error).

    Returns:
        The canonical status actually emitted ("ok" | "warn" | "error") — handy for assertions/tests.
        Returns "error" if the sink itself failed internally (best-effort), never raises.
    """
    try:
        canon = status if status in _STATUS_LEVEL else "error"
        level = _STATUS_LEVEL[canon]
        line = f"OBSERV {_safe(scope, 80)}.{_safe(event, 80)} status={canon}{_format_kv(fields)}"
        if note:
            line += f" — {_safe(note, 300)}"
        logger.log(level, line)
        if canon == "error":
            _escalate_to_slack(scope, event, line)  # fail-safe; no-op unless configured
        return canon
    except Exception:  # noqa: BLE001 - the sink must NEVER raise into the phase
        # best-effort: don't lose a data-affecting signal just because logging broke
        try:
            sys.stderr.write(
                f"OBSERV {_safe(scope, 80)}.{_safe(event, 80)} status=error "
                "(observe-sink-failed)\n"
            )
        except Exception:  # noqa: BLE001
            pass
        return "error"


def observe_error(scope: str, event: str, *, note: str = "", **fields: Any) -> str:
    """Convenience: a data-affecting FAILURE at the point of loss (status=error). Never raises.

    Use this in `except` handlers and parse/validation-failed branches that return an empty/None
    sentinel — so the drop is a loud, greppable anomaly instead of a silent zero (PATTERN-8).
    """
    return observe(scope, event, status="error", note=note, **fields)


def observe_warn(scope: str, event: str, *, note: str = "", **fields: Any) -> str:
    """Convenience: a degraded-but-recovered path (status=warn). Never raises."""
    return observe(scope, event, status="warn", note=note, **fields)
