"""_log_api_response must not raise when the response has no ``choices``.

Found by basedpyright (reportPossiblyUnbound): the deprecation preview reads ``content``, which was
only assigned inside ``if "choices" ...``. A response with no choices that is served by a different
model than requested (an error payload during a model redirect) raised UnboundLocalError from a
logging helper in the client every extractor depends on.
"""

from src.grok_client import GrokClient


def _client(monkeypatch):
    c = object.__new__(GrokClient)
    c.model = "grok-requested"
    c._deprecation_alerted = False
    c.debug_resp_chars = 50
    monkeypatch.setattr(c, "_log_token_usage", lambda r: None)
    alerts = []
    monkeypatch.setattr(c, "_send_deprecation_alert", alerts.append)
    return c, alerts


def test_no_choices_with_redirected_model_does_not_raise(monkeypatch):
    c, alerts = _client(monkeypatch)
    c._log_api_response({"error": "overloaded", "model": "grok-other"})
    assert alerts == ["grok-other"]  # the deprecation alert still fires


def test_empty_choices_does_not_raise(monkeypatch):
    c, _ = _client(monkeypatch)
    c._log_api_response({"choices": [], "model": "grok-other"})


def test_normal_response_unchanged(monkeypatch):
    c, alerts = _client(monkeypatch)
    c._log_api_response(
        {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "model": "grok-requested",
        }
    )
    assert alerts == []


def test_null_content_does_not_raise(monkeypatch):
    """A refusal / tool-call response carries content=null (key present, value None)."""
    c, _ = _client(monkeypatch)
    c._log_api_response(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "tool_calls"}],
            "model": "grok-requested",
        }
    )
