"""Tests for the prompt-level batched verifier ``_verify_results_batch`` (design doc §10/§10A).

Covers: correct per-result bools in order; fail-closed on malformed/short arrays and on no
client / Grok error; cache hits avoid any Grok call; cache + verify metrics written per newly
verified result (keyed exactly as the single-item ``_verify_result``). Grok is mocked — no
network.
"""

from unittest.mock import MagicMock, patch

import src.enrichment.openserp_enrichment as oe


def _results(n):
    return [
        {"url": f"http://u{i}", "title": f"t{i}", "description": f"d{i}"}
        for i in range(n)
    ]


def test_batch_returns_per_result_bools_in_order():
    oe.reset_metrics()
    grok = MagicMock()
    grok.chat_completion.return_value = '["YES", "NO", "YES"]'
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result") as mock_cache,
    ):
        verdicts = oe._verify_results_batch("ctx", _results(3), grok)
    assert verdicts == [True, False, True]
    # exactly ONE Grok call for all three results
    assert grok.chat_completion.call_count == 1
    # per-result cache writes + metrics
    assert mock_cache.call_count == 3
    snap = oe.get_metrics()
    assert snap["verify_yes"] == 2 and snap["verify_no"] == 1


def test_batch_cache_key_matches_single_item_path():
    """The batched path must key the cache EXACTLY like _verify_result:
    f"{context[:50]}|{title[:50]}|{url[:60]}"."""
    grok = MagicMock()
    grok.chat_completion.return_value = '["YES"]'
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result") as mock_cache,
    ):
        oe._verify_results_batch(
            "Photo of X", [{"url": "http://u", "title": "t", "description": "d"}], grok
        )
    source, key, answer = mock_cache.call_args.args
    assert source == "openserp_verify"
    assert key == "Photo of X|t|http://u"
    assert answer == "YES"


def test_batch_fail_closed_on_short_array():
    """A response shorter than the number of results -> missing indices are NO (fail-closed)."""
    oe.reset_metrics()
    grok = MagicMock()
    grok.chat_completion.return_value = '["YES"]'  # only 1 of 3
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
    ):
        verdicts = oe._verify_results_batch("ctx", _results(3), grok)
    assert verdicts == [True, False, False]


def test_batch_fail_closed_on_malformed_response():
    oe.reset_metrics()
    grok = MagicMock()
    grok.chat_completion.return_value = "garbage not an array"
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
    ):
        verdicts = oe._verify_results_batch("ctx", _results(2), grok)
    assert verdicts == [False, False]


def test_batch_fail_closed_on_grok_error_not_cached():
    oe.reset_metrics()
    grok = MagicMock()
    grok.chat_completion.side_effect = RuntimeError("boom")
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result") as mock_cache,
    ):
        verdicts = oe._verify_results_batch("ctx", _results(2), grok)
    assert verdicts == [False, False]
    # errored verdicts are NOT cached (so a healthy later run can re-verify)
    assert not mock_cache.called
    snap = oe.get_metrics()
    assert snap["verify_yes"] == 0 and snap["verify_no"] == 0


def test_batch_no_client_rejects_all():
    verdicts = oe._verify_results_batch("ctx", _results(4), grok_client=None)
    assert verdicts == [False, False, False, False]


def test_batch_empty_results():
    assert oe._verify_results_batch("ctx", [], grok_client=MagicMock()) == []


def test_batch_all_cached_issues_no_grok_call():
    oe.reset_metrics()
    grok = MagicMock()

    def cached(_source, key):
        # u0 -> YES, u1 -> NO
        return "YES" if key.endswith("http://u0") else "NO"

    with (
        patch("src.utils.search_cache.get_cached", side_effect=cached),
        patch("src.utils.search_cache.cache_result") as mock_cache,
    ):
        verdicts = oe._verify_results_batch("ctx", _results(2), grok)
    assert verdicts == [True, False]
    assert grok.chat_completion.call_count == 0  # fully served from cache
    assert not mock_cache.called  # cache hits are not re-written
    # cache hits do NOT re-increment metrics (matches single-item path)
    snap = oe.get_metrics()
    assert snap["verify_yes"] == 0 and snap["verify_no"] == 0


def test_batch_partial_cache_only_verifies_uncached():
    oe.reset_metrics()
    grok = MagicMock()
    grok.chat_completion.return_value = '["YES"]'  # for the single uncached result

    def cached(_source, key):
        return "YES" if key.endswith("http://u0") else None  # u0 cached, u1 uncached

    with (
        patch("src.utils.search_cache.get_cached", side_effect=cached),
        patch("src.utils.search_cache.cache_result") as mock_cache,
    ):
        verdicts = oe._verify_results_batch("ctx", _results(2), grok)
    assert verdicts == [True, True]
    assert grok.chat_completion.call_count == 1
    # only the uncached result is written to the cache
    assert mock_cache.call_count == 1
    assert mock_cache.call_args.args[1].endswith("http://u1")


def test_batch_prose_response_is_fail_closed():
    """A non-JSON prose response (even if it contains YES/NO words) must NOT be token-scanned;
    it fails closed to all-NO so verdicts can never misalign to results."""
    import src.enrichment.openserp_enrichment as oe

    class _G:
        def chat_completion(self, *a, **k):
            return "Yes the first looks relevant, no on the second one."

    results = [
        {"title": "A", "url": "http://a", "description": "x"},
        {"title": "B", "url": "http://b", "description": "y"},
    ]
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
    ):
        verdicts = oe._verify_results_batch("ctx", results, _G())
    assert verdicts == [False, False]  # prose -> fail-closed, not [True, False]
