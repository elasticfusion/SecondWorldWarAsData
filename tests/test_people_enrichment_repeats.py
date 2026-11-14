"""A person mentioned many times is enriched once (2026-11-14 full-book run: "Enriching (phase 2):
Eisenhower" per mention per round, each paying a 5 s Wikipedia wait even on a cache hit).
"""

from unittest import mock

from src.extraction import enrich_biographies as E


def test_an_enriched_person_is_skipped_even_without_a_birth_date():
    assert E._is_already_enriched(
        {
            "enrichment_status": "enriched",
            "biographical_profile": {"ranks": ["General"]},
        }
    )


def test_a_person_not_yet_enriched_is_not_skipped():
    assert not E._is_already_enriched({"name": "X", "biographical_profile": {}})


def test_a_cached_wikipedia_answer_waits_for_no_rate_limit_slot():
    with (
        mock.patch("src.utils.search_cache.get_cached", return_value="cached bio"),
        mock.patch.object(E, "_wikipedia_rate_limit") as wait,
    ):
        assert E.search_wikipedia("Eisenhower") == "cached bio"
    wait.assert_not_called()
    with (
        mock.patch("src.utils.search_cache.get_cached", return_value="NOT_FOUND"),
        mock.patch.object(E, "_wikipedia_rate_limit") as wait,
    ):
        assert E.search_wikipedia("Nobody") is None
    wait.assert_not_called()
