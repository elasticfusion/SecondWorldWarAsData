"""Regression: inner extract_fns must PROPAGATE GrokTruncationError to the chunk halver.

Before: casualties/weather/logistics inner fns caught `except Exception` (which includes
GrokTruncationError), retried in-place, and returned {} — so truncation never reached
extract_with_chunk_halving and the WHOLE chunk was silently dropped (and PR #488's recursive halving
was latent). Now they re-raise GrokTruncationError (like BatchModeCollecting) so the halver recovers.
"""

from unittest import mock

import pytest

from src.grok_client import GrokTruncationError, BatchModeCollecting


# --- casualties ----------------------------------------------------------------------------
def test_casualties_reraises_truncation():
    from src.extraction.casualties import _call_and_parse_casualties

    client = mock.Mock()
    client._get_model.return_value = "m"
    client.chat_completion.side_effect = GrokTruncationError("truncated")
    with pytest.raises(GrokTruncationError):
        _call_and_parse_casualties(client, "prompt")


def test_casualties_still_returns_empty_on_non_truncation():
    from src.extraction.casualties import _call_and_parse_casualties

    client = mock.Mock()
    client._get_model.return_value = "m"
    client.chat_completion.side_effect = RuntimeError("other")
    assert _call_and_parse_casualties(client, "prompt") == {}  # unchanged leniency


# --- weather -------------------------------------------------------------------------------
def test_weather_reraises_truncation():
    from src.extraction.weather_central import _call_and_parse_weather

    client = mock.Mock()
    client.extract_json.side_effect = GrokTruncationError("truncated")
    with pytest.raises(GrokTruncationError):
        _call_and_parse_weather(client, "prompt", max_retries=3)


# --- logistics -----------------------------------------------------------------------------
def test_logistics_reraises_truncation():
    from src.extraction.logistics import _extract_logistics_chunk

    client = mock.Mock()
    client.extract_json.side_effect = GrokTruncationError("truncated")
    chunk = [("01SUB00000000000000000000A0", "some logistics text")]
    with pytest.raises(GrokTruncationError):
        _extract_logistics_chunk(chunk, client)


# --- end-to-end: the re-raise makes the halver actually recover -----------------------------
def test_truncation_now_recovers_via_halver_not_empty():
    """An extract_fn that truncates at size>1 but succeeds at size-1 must now recover ALL items
    through extract_with_chunk_halving — previously the inner-fn swallow returned {} (whole chunk
    lost)."""
    from src.utils.chunked_extract import extract_with_chunk_halving

    def inner(chunk):
        if len(chunk) > 1:
            raise GrokTruncationError(
                "truncated"
            )  # re-raised by the real inner fns now
        return {chunk[0]: chunk[0].upper()}

    out = extract_with_chunk_halving([["a", "b", "c", "d"]], inner, "test")
    assert out == {c: c.upper() for c in "abcd"}


def test_batchmodecollecting_still_propagates_from_inner_fns():
    from src.extraction.casualties import _call_and_parse_casualties

    client = mock.Mock()
    client._get_model.return_value = "m"
    client.chat_completion.side_effect = BatchModeCollecting("queued")
    with pytest.raises(BatchModeCollecting):
        _call_and_parse_casualties(client, "prompt")
