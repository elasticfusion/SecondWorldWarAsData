"""Tests for the generic observability sink (src/utils/observe.py).

Covers the two owner requirements:
  (1) it must NEVER stop progress — observe() cannot raise, even on a broken logger / bad args;
  (2) it must be QUERYABLE — a stable, greppable `OBSERV <scope>.<event> status=...` grammar.
"""

import logging
import re

import pytest

from src.utils.observe import observe, observe_error, observe_warn

# ---- (2) QUERYABLE: stable grammar -------------------------------------------------------------

CANON = re.compile(
    r"^OBSERV (?P<scope>\w+)\.(?P<event>\w+) status=(?P<status>ok|warn|error)"
)


def test_emits_canonical_grammar(caplog):
    with caplog.at_level(logging.INFO, logger="observe"):
        observe("groups", "extract", status="ok", produced=74, event_id="E1")
    m = CANON.match(caplog.records[-1].message)
    assert m and m.group("scope") == "groups" and m.group("event") == "extract"
    assert m.group("status") == "ok"
    assert "produced=74" in caplog.text and "event_id=E1" in caplog.text


def test_status_maps_to_level(caplog):
    with caplog.at_level(logging.INFO, logger="observe"):
        observe("a", "x", status="ok")
        observe("a", "y", status="warn")
        observe("a", "z", status="error")
    levels = {r.message.split()[1]: r.levelno for r in caplog.records}
    assert levels["a.x"] == logging.INFO
    assert levels["a.y"] == logging.WARNING
    assert levels["a.z"] == logging.ERROR


def test_unknown_status_coerced_to_error(caplog):
    # fail loud: an invalid status must not silently become ok
    with caplog.at_level(logging.INFO, logger="observe"):
        rv = observe("a", "b", status="bogus")
    assert rv == "error" and "status=error" in caplog.text


def test_produced_zero_is_present_so_failure_zero_is_distinguishable(caplog):
    # the PATTERN-8 defense: a failure zero carries produced=0 + status=error
    with caplog.at_level(logging.ERROR, logger="observe"):
        observe_error(
            "groups", "batch_error", reason="ValueError", produced=0, event_id="E9"
        )
    assert "status=error" in caplog.text and "produced=0" in caplog.text


def test_fields_sanitized_single_line_and_bounded(caplog):
    with caplog.at_level(logging.WARNING, logger="observe"):
        observe_warn("x", "y", reason="a messy\nmulti line\treason", blob="z" * 500)
    msg = caplog.records[-1].message
    assert "\n" not in msg and "\t" not in msg  # single greppable line
    assert "reason=a_messy_multi_line_reason" in msg
    assert "..." in msg  # long value truncated


def test_none_fields_omitted(caplog):
    with caplog.at_level(logging.INFO, logger="observe"):
        observe("x", "y", status="ok", present=1, absent=None)
    assert "present=1" in caplog.text and "absent=" not in caplog.text


# ---- (1) NEVER STOPS PROGRESS: fail-safe -------------------------------------------------------


def test_never_raises_on_broken_logger(monkeypatch):
    # even if the underlying logger explodes, observe() must swallow and return "error"
    import src.utils.observe as obs

    def boom(*_a, **_k):
        raise RuntimeError("logging backend down")

    monkeypatch.setattr(obs.logger, "log", boom)
    rv = obs.observe("x", "y", status="error", produced=0)  # must NOT raise
    assert rv == "error"


def test_never_raises_on_weird_args():
    # non-string scope/event, unstringifiable field — still no exception
    class Unstr:
        def __str__(self):
            raise ValueError("cannot stringify")

    # scope/event are used in an f-string; a bad field value is coerced inside the try
    assert observe("x", "y", status="ok", bad=Unstr()) in ("ok", "error")


# ---- integration: the batch_parallel rewire still emits via the sink ---------------------------


def test_batch_parallel_drop_routes_through_sink(caplog):
    import asyncio
    from pathlib import Path
    from unittest.mock import MagicMock
    from src.extraction import batch_parallel as BP

    grok = MagicMock()
    grok.extract_json.side_effect = ValueError("Extra data: line 1 column 4970")
    with caplog.at_level(logging.ERROR, logger="observe"):
        asyncio.run(
            BP._batch_extract(
                {"Event": {"EventID": "E123", "Sub-events": [{"Sub-eventID": "S1"}]}},
                grok,
                Path("/tmp"),
                "people_groups",
                "peoplegroups",
                "header",
                "groups",
                "groups",
                (lambda o: "k"),
                (lambda o: {}),
            )
        )
    assert "OBSERV people_groups.batch_error status=error" in caplog.text
    assert "produced=0" in caplog.text and "event_id=E123" in caplog.text


# ---- Slack escalation (the "output to Slack" out) ----------------------------------------------


def _reset_slack_state():
    import src.utils.observe as obs

    obs._slack_last_sent.clear()


def test_no_slack_when_topic_unset(monkeypatch):
    import src.utils.observe as obs

    _reset_slack_state()
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    published = []
    monkeypatch.setattr(obs, "_escalate_to_slack", lambda *a, **k: published.append(a))
    # even an error must not escalate when unset (handled inside _escalate; here assert gate)
    assert obs._slack_enabled() is False


def test_error_escalates_once_and_rate_limits(monkeypatch):
    import src.utils.observe as obs

    _reset_slack_state()
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:notify")
    monkeypatch.setenv("OBSERVE_SLACK", "1")
    sent = []

    class FakeSNS:
        def publish(self, **kw):
            sent.append(kw)

    import sys as _sys, types

    fake_boto3 = types.SimpleNamespace(client=lambda *a, **k: FakeSNS())
    monkeypatch.setitem(_sys.modules, "boto3", fake_boto3)

    obs.observe_error("groups", "batch_error", produced=0)  # 1st -> escalates
    obs.observe_error(
        "groups", "batch_error", produced=0
    )  # 2nd same key -> rate-limited
    assert len(sent) == 1
    assert "OBSERV groups.batch_error status=error" in sent[0]["Message"]


def test_ok_and_warn_never_escalate(monkeypatch):
    import src.utils.observe as obs

    _reset_slack_state()
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:notify")
    calls = []
    monkeypatch.setattr(obs, "_escalate_to_slack", lambda *a, **k: calls.append(a))
    obs.observe("x", "y", status="ok")
    obs.observe("x", "z", status="warn")
    assert calls == []


def test_slack_publish_error_is_swallowed(monkeypatch):
    import src.utils.observe as obs

    _reset_slack_state()
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:notify")
    import sys as _sys, types

    def boom(*a, **k):
        raise RuntimeError("sns down")

    monkeypatch.setitem(_sys.modules, "boto3", types.SimpleNamespace(client=boom))
    # must NOT raise despite the publish exploding
    assert obs.observe_error("x", "y", produced=0) == "error"


# ---- log-injection hardening (SECURITY-622 finding) --------------------------------------------


def _observ_line_count(text: str) -> int:
    # count physical lines containing the OBSERV token (caplog prefixes level/logger/path, so the
    # message is not at col 0). A forged-injection line would add a SECOND such physical line.
    return sum(1 for ln in text.splitlines() if "OBSERV " in ln)


def test_newline_in_scope_cannot_forge_a_line(caplog):
    with caplog.at_level(logging.INFO, logger="observe"):
        observe("groups\nOBSERV fake.injected status=ok produced=999", "x", status="ok")
    # exactly ONE OBSERV line; the injected text is collapsed into the scope token (underscored),
    # NOT a second parseable line with its own leading "OBSERV ".
    assert _observ_line_count(caplog.text) == 1
    assert (
        "groups_OBSERV_fake.injected_status=ok" in caplog.text
    )  # neutralized into one token


def test_newline_in_event_and_note_cannot_forge(caplog, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    with caplog.at_level(logging.ERROR, logger="observe"):
        observe(
            "a",
            "batch_error\nOBSERV fake.e status=ok",
            status="error",
            note="boom\nOBSERV forged.note status=ok produced=42",
        )
    # one line; the real status is error; the injected status=ok lives only inside collapsed tokens,
    # never as a forged standalone OBSERV line.
    assert _observ_line_count(caplog.text) == 1
    line = next(ln for ln in caplog.text.splitlines() if "OBSERV " in ln)
    assert "status=error" in line and "_OBSERV_fake.e_status=ok" in line


def test_unicode_line_separators_collapsed(caplog):
    with caplog.at_level(logging.WARNING, logger="observe"):
        observe("x", "y", status="warn", reason="a\u2028b\u0085c\u2029d")
    msg = caplog.records[-1].message
    assert "\u2028" not in msg and "\u2029" not in msg and "\u0085" not in msg
    assert "reason=a_b_c_d" in msg
