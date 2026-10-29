"""Regression tests for the grok_client JSON total-loss observability hole (PATTERN-8).

Audit findings #1 and #2 (OBSERVABILITY_PATTERN8_AUDIT.md): a JSON parse/repair total-loss in
``GrokClient`` returned/raised a GENERIC error (or a silent ``None``) WITHOUT a loud, greppable
``OBSERV grok.* status=error`` at the point of loss — so the drop was indistinguishable from a
legitimate empty result (the exact PATTERN-8 that zeroed the people batch 82->0 on 2026-10-29).

These tests pin the fix:
  #1 ``_handle_json_decode_error`` — when repair fails and it raises ``GrokAPIError``, it FIRST
     emits ``OBSERV grok.json_failed status=error`` (then still raises).
  #2 ``_merge_concatenated_json`` — a fully-garbage concat response (nothing decodes) emits
     ``OBSERV grok.concat_json_unrecoverable status=error`` instead of silently returning ``None``.

The observe sink logs to the logger named "observe"; caplog is set at root level to capture it.
``NOTIFICATION_TOPIC_ARN`` is deleted so the status=error Slack escalation is a no-op (offline).
"""

import json
import logging
from unittest.mock import patch

import pytest

from src.grok_client import GrokAPIError, GrokClient


@pytest.fixture(autouse=True)
def _offline_observe(monkeypatch):
    """Keep observe()'s status=error Slack escalation offline for every test in this module."""
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


def _observ_lines(caplog, event):
    """All captured ``OBSERV grok.<event> status=error`` message lines."""
    needle = f"OBSERV grok.{event} status=error"
    return [r.message for r in caplog.records if needle in r.message]


class TestJsonFailedObserv:
    """Finding #1: an unrepairable JSON decode failure is a LOUD OBSERV, not generic-only."""

    def test_decode_failure_emits_json_failed(self, tmp_path, caplog):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        # A >=500-char response so the short-response branch is skipped, with a decode-error message
        # that matches NEITHER the truncation keywords ("Unterminated"/"Expecting") NOR the escape-
        # repair branch ("Invalid"+"escape") nor "Extra data" — so it routes to the total-loss raise.
        response = "{" + "x" * 600
        err = json.JSONDecodeError("Invalid control character at", response, 5)

        with (
            caplog.at_level(logging.ERROR, logger="observe"),
            patch.object(client, "_get_cache", return_value={}),
            patch.object(client, "clear_cache_entry"),
            patch.object(client, "_make_cache_key", return_value="k"),
        ):
            with pytest.raises(GrokAPIError):
                client._handle_json_decode_error(
                    response, err, "events", "prompt", 0.1, True
                )

        lines = _observ_lines(caplog, "json_failed")
        assert (
            lines
        ), "expected a LOUD OBSERV grok.json_failed status=error at the point of loss"
        line = lines[0]
        assert "reason=JSONDecodeError" in line
        assert f"length={len(response)}" in line
        assert "cache_type=events" in line


class TestConcatUnrecoverableObserv:
    """Finding #2: a total concat-JSON loss is a LOUD OBSERV, not a silent None."""

    def test_fully_garbage_concat_emits_unrecoverable(self, tmp_path, caplog):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        # Trailing garbage so _decode_all_objects flags `truncated`, but NOTHING decodes -> the
        # genuine total-loss branch (not the normal single-object fall-through).
        with caplog.at_level(logging.ERROR, logger="observe"):
            result = client._merge_concatenated_json("not json at all <<<>>>")

        assert result is None
        lines = _observ_lines(caplog, "concat_json_unrecoverable")
        assert (
            lines
        ), "expected a LOUD OBSERV grok.concat_json_unrecoverable on total concat loss"
        assert "reason=no_objects_decoded" in lines[0]

    def test_single_clean_object_does_not_emit(self, tmp_path, caplog):
        """A normal single clean object falls through to the caller as None WITHOUT an OBSERV —
        the non-concatenated happy path must NOT be flagged as a loss (no false positive).
        """
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        with caplog.at_level(logging.ERROR, logger="observe"):
            result = client._merge_concatenated_json('{"people": [1, 2, 3]}')

        assert result is None  # single object -> caller parses it directly
        assert not _observ_lines(caplog, "concat_json_unrecoverable")

    def test_merge_exception_emits_unrecoverable(self, tmp_path, caplog):
        """The fail-safe ``except Exception`` path (repair blew up) must also be LOUD before it
        swallows and returns None."""
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        with (
            caplog.at_level(logging.ERROR, logger="observe"),
            patch.object(
                client, "_decode_all_objects", side_effect=RuntimeError("boom")
            ),
        ):
            result = client._merge_concatenated_json('{"a": 1}{"a": 2}')

        assert result is None
        lines = _observ_lines(caplog, "concat_json_unrecoverable")
        assert (
            lines
        ), "expected a LOUD OBSERV on the fail-safe exception total-loss path"
        assert "reason=RuntimeError" in lines[0]
