"""The shared judge-then-enqueue helper (Group 3): a find is queued only with a positive Grok verdict,
once per (provider, origin, URL), carrying its origin; a search call is never a review item.
"""

import json
import pathlib
import tempfile

import pytest

from src.utils import fetch_provenance as fp
from src.utils import find_queue as fq


@pytest.fixture
def store(monkeypatch):
    d = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(fp, "_LOCAL_DIR", d)
    monkeypatch.setattr(fp, "_LOCAL_SEEN", {})
    return d


def _events(d):
    return [
        json.loads(line)
        for f in d.glob("*.jsonl")
        for line in f.read_text().splitlines()
        if line.strip()
    ]


GROUP = fq.Origin(
    entity_type="people_group", entity_id="01HGROUP", query="3rd Armored Division"
)


def _enqueue(
    url="https://example.org/3ad",
    verdict=fq.RELEVANT,
    origin=GROUP,
    provider="openserp-find",
):
    return fq.enqueue_find(
        provider=provider, url=url, verdict=verdict, origin=origin, summary="s"
    )


def test_a_relevant_find_is_queued_pending_with_its_origin(store):
    assert _enqueue() == fq.ENQUEUED
    (e,) = _events(store)
    assert e["review_status"] == fp.PENDING and e["provider"] == "openserp-find"
    p = e["request_params"]
    assert (p["origin_entity_type"], p["origin_entity_id"], p["query"]) == (
        "people_group",
        "01HGROUP",
        "3rd Armored Division",
    )
    assert p["relevancy"] == "relevant"


@pytest.mark.parametrize(
    "verdict", ["irrelevant", "undecided", "", "YES", "whitelisted"]
)
def test_a_find_without_a_positive_grok_verdict_is_never_queued(store, verdict):
    assert _enqueue(verdict=verdict) == fq.REFUSED
    assert _events(store) == []


def test_an_unsafe_url_and_an_unknown_provider_are_refused(store, caplog):
    assert _enqueue(url="http://169.254.169.254/latest") == fq.REFUSED
    assert _enqueue(url="not a url") == fq.REFUSED
    assert (
        _enqueue(provider="nara") == fq.REFUSED
    )  # a SEARCH provider is not a find provider
    assert "find_queue.bad_provider" in caplog.text and _events(store) == []


def test_a_later_run_does_not_queue_the_same_find_again(store):
    assert _enqueue() == fq.ENQUEUED
    assert _enqueue() == fq.ALREADY_QUEUED
    assert len(_events(store)) == 1


def test_the_same_url_found_for_another_entity_is_its_own_item(store):
    """Relevance is per (URL, entity): another unit asking about the same page is judged and queued
    on its own."""
    assert _enqueue() == fq.ENQUEUED
    other = fq.Origin(
        entity_type="people_group", entity_id="01HOTHER", query="4th Armored"
    )
    assert _enqueue(origin=other) == fq.ENQUEUED
    assert len(_events(store)) == 2


def test_a_rejected_find_is_not_queued_again(store):
    from src.enrichment import fetch_review

    _enqueue()
    (event,) = _events(store)
    assert fetch_review.reject_fetch_find(event)
    assert _enqueue() == fq.ALREADY_QUEUED
    assert [e["review_status"] for e in _events(store)] == [fp.REJECTED]


def test_a_document_origin_is_keyed_by_book_and_source():
    a = fq.Origin(book="B", source_url="u1")
    assert a.key == "B|u1" and fq.Origin(entity_id="E", book="B").key == "E"


def test_search_calls_are_provenance_only(store, monkeypatch):
    """recorded_fetch of an archive.org / NARA / Gutendex query no longer queues a review item."""
    for provider in ("nara", "archive-org", "archive-solr", "gutendex"):
        fp.record_fetch_event(
            provider=provider,
            endpoint="https://example.org/search",
            kind=fp.TEXTUAL,
            outcome=fp.HIT,
        )
    assert {e["review_status"] for e in _events(store)} == {None}
    assert len(_events(store)) == 4


def test_every_find_provider_is_review_eligible():
    assert (
        fp.FIND_PROVIDERS <= fp._ENQUEUE_PROVIDERS and "link-asset" in fp.FIND_PROVIDERS
    )


def test_an_arrow_or_control_character_in_the_summary_is_defused(store):
    fq.enqueue_find(
        provider="openserp-find",
        url="https://example.org/a",
        verdict=fq.RELEVANT,
        origin=fq.Origin(entity_id="01H", query="q\x00\n->evil"),
        summary="a -> https://evil.example/\x07b",
    )
    (e,) = _events(store)
    assert e["result_summary"].count("->") == 0
    assert (
        "\x07" not in e["result_summary"] and "\x00" not in e["request_params"]["query"]
    )
    assert "->" not in e["request_params"]["query"]


def test_a_legacy_event_blocks_the_find(store):
    fp.record_fetch_event(
        provider="link-asset",
        endpoint="u",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
        once_key=("b", "s", "https://example.org/a"),
    )
    legacy = ("b", "s", "https://example.org/a")
    org = fq.Origin(book="b", source_url="s")
    assert fq.already_queued("link-asset", "https://example.org/a", org, legacy)
    assert not fq.already_queued("link-asset", "https://example.org/zzz", org)
    assert (
        fq.enqueue_find(
            provider="link-asset",
            url="https://example.org/a",
            verdict=fq.RELEVANT,
            origin=org,
            summary="s",
            legacy_key=legacy,
        )
        == fq.ALREADY_QUEUED
    )
