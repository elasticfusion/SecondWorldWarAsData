"""Tests for the OpenSERP verifiers (task 1.2e, owner 2026-11-18).

Both verifiers go through the shared Grok client's JSON path (``extract_json``: repair, re-ask)
instead of parsing raw text. Owner rule 3: a Grok failure is never a "NO". A real verdict is
cached; no usable verdict raises ``VerifyFailed`` (nothing cached, the entity left unsearched for
the next run); a Grok OUTAGE stops the run (``grok_models.GrokUnreachable`` passes through).
Grok is mocked - no network.
"""

from unittest.mock import MagicMock, patch

import pytest

import src.enrichment.openserp_enrichment as oe
from src.grok_client import GrokParseError
from src.utils import grok_models


def _results(n):
    return [
        {"url": f"http://u{i}", "title": f"t{i}", "description": f"d{i}"}
        for i in range(n)
    ]


def _grok(answer=None, error=None):
    grok = MagicMock()
    if error is not None:
        grok.extract_json.side_effect = error
    else:
        grok.extract_json.return_value = answer
    return grok


@pytest.fixture(autouse=True)
def _fresh():
    oe.reset_metrics()
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result") as cache,
    ):
        yield cache


# --- batch -------------------------------------------------------------------------------------


def test_batch_returns_per_result_bools_in_order(_fresh):
    grok = _grok({"verdicts": ["YES", "NO", "yes"]})
    assert oe._verify_results_batch("ctx", _results(3), grok) == [True, False, True]
    assert grok.extract_json.call_count == 1  # one Grok call for all three
    assert grok.extract_json.call_args.kwargs["cache_type"] == "openserp_verify_batch"
    assert _fresh.call_count == 3
    snap = oe.get_metrics()
    assert snap["verify_yes"] == 2 and snap["verify_no"] == 1


def test_batch_cache_key_matches_single_item_path(_fresh):
    oe._verify_results_batch(
        "Photo of X",
        [{"url": "http://u", "title": "t", "description": "d"}],
        _grok({"verdicts": ["YES"]}),
    )
    assert _fresh.call_args.args == (
        "openserp_verify",
        oe._verify_key("Photo of X", "t", "http://u"),
        "YES",
    )


@pytest.mark.parametrize(
    "answer",
    [{"verdicts": ["YES"]},  # short
     {"verdicts": ["YES", "maybe"]},  # ambiguous
     {"other": 1}, "garbage", None],
)  # fmt: skip
def test_batch_without_a_full_clear_answer_is_a_failure_not_nos(_fresh, answer):
    with pytest.raises(oe.VerifyFailed):
        oe._verify_results_batch("ctx", _results(2), _grok(answer))
    assert not _fresh.called  # nothing cached: the next run asks again
    snap = oe.get_metrics()
    assert snap["verify_no"] == 0 and snap["verify_failed"] == 2


def test_batch_unrepairable_answer_is_a_failure(_fresh):
    with pytest.raises(oe.VerifyFailed):
        oe._verify_results_batch(
            "ctx", _results(2), _grok(error=GrokParseError("prose"))
        )
    assert not _fresh.called


def test_a_grok_outage_stops_the_run_instead_of_rejecting(_fresh):
    with pytest.raises(grok_models.GrokUnreachable):
        oe._verify_results_batch(
            "ctx", _results(2), _grok(error=grok_models.GrokUnreachable("down"))
        )
    assert not _fresh.called


def test_batch_no_client_rejects_all():
    assert oe._verify_results_batch("ctx", _results(4), grok_client=None) == [False] * 4


def test_batch_empty_results():
    assert oe._verify_results_batch("ctx", [], grok_client=MagicMock()) == []


def test_batch_all_cached_issues_no_grok_call(_fresh):
    grok = _grok()
    with patch(
        "src.utils.search_cache.get_cached",
        side_effect=lambda _s, key: "YES" if key.endswith('http://u0"]') else "NO",
    ):
        assert oe._verify_results_batch("ctx", _results(2), grok) == [True, False]
    assert grok.extract_json.call_count == 0 and not _fresh.called


def test_batch_partial_cache_only_verifies_uncached(_fresh):
    grok = _grok({"verdicts": ["YES"]})
    with patch(
        "src.utils.search_cache.get_cached",
        side_effect=lambda _s, key: "YES" if key.endswith('http://u0"]') else None,
    ):
        assert oe._verify_results_batch("ctx", _results(2), grok) == [True, True]
    assert _fresh.call_count == 1 and _fresh.call_args.args[1].endswith('http://u1"]')


# --- single ------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,want", [("YES", True), ("no", False)])
def test_a_single_verdict_is_cached_and_returned(_fresh, raw, want):
    grok = _grok({"relevant": raw})
    assert oe._verify_result("t", "ctx", grok, url="http://u") is want
    assert _fresh.call_args.args == (
        "openserp_verify",
        oe._verify_key("ctx", "t", "http://u"),
        raw.upper(),
    )


@pytest.mark.parametrize("answer", [{"relevant": "perhaps"}, {}, "YES", None])
def test_a_single_unclear_answer_is_a_failure_not_a_no(_fresh, answer):
    with pytest.raises(oe.VerifyFailed):
        oe._verify_result("t", "ctx", _grok(answer))
    assert not _fresh.called and oe.get_metrics()["verify_failed"] == 1


def test_a_single_grok_error_is_a_failure_not_a_no(_fresh):
    from src.grok_client import GrokAPIError

    with pytest.raises(oe.VerifyFailed):
        oe._verify_result("t", "ctx", _grok(error=GrokAPIError("bad")))
    assert not _fresh.called


# --- drivers: a failed verdict leaves the entity unsearched ----------------------------------


def test_a_failed_verdict_leaves_the_place_unsearched(tmp_path, monkeypatch):
    import json

    place = tmp_path / "p.json"
    place.write_text(json.dumps({"name": "Bastogne"}))
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(
        oe,
        "enrich_one_place",
        MagicMock(side_effect=oe.VerifyFailed("no verdict")),
    )
    writes = []
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: writes.append(a))
    assert oe.enrich_places_with_openserp(tmp_path, "http://serp", MagicMock()) == 0
    assert writes == []  # not marked searched, not written: the next run asks again
    assert "openserp_searched" not in json.loads(place.read_text())


def test_a_wrong_shape_answer_is_evicted_and_asked_once_more(_fresh):
    """Review finding: extract_json caches well-formed JSON, so a cached wrong-shape answer would
    fail the same way every run. It is evicted and re-asked; a good second answer is used.
    """
    grok = MagicMock()
    grok.extract_json.side_effect = [{"relevant": "maybe"}, {"relevant": "YES"}]
    assert oe._verify_result("t", "ctx", grok) is True
    assert grok.clear_cache_entry.call_count == 1
    assert oe.get_metrics()["verify_reasked"] == 1


def test_two_wrong_shape_answers_fail_and_neither_is_kept(_fresh):
    grok = MagicMock()
    grok.extract_json.return_value = {"verdicts": ["YES"]}
    with pytest.raises(oe.VerifyFailed):
        oe._verify_results_batch("ctx", _results(2), grok)
    assert grok.clear_cache_entry.call_count == 2 and not _fresh.called
