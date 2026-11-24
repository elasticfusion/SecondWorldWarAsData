"""Enqueue POLICY: only FINDS (a URL a step found and Grok judged relevant: ``fp.FIND_PROVIDERS``) and
recovered Wayback documents are enqueued for human review; encyclopedia/enrichment textual lookups (wikipedia,
grokipedia, wikimedia-commons, openserp) record provenance only (review_status=None). Plus the
reject verb and the affirm/reject persistence.
"""

import json
from pathlib import Path

import pytest

from src.enrichment import fetch_review
from src.utils import fetch_provenance as fp


@pytest.fixture(autouse=True)
def _local(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_table", lambda: None)
    monkeypatch.setattr(fp, "_LOCAL_DIR", tmp_path / "_fe")
    return tmp_path / "_fe"


def _events(store, provider):
    f = store / f"{provider}.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_primary_source_providers_are_enqueued():
    for prov in sorted(fp.FIND_PROVIDERS):
        assert fp._initial_review_status(prov, fp.TEXTUAL, fp.HIT) == fp.PENDING


def test_a_search_call_is_never_a_review_item():
    """nara / archive.org / gutendex queries used to queue one item per successful query."""
    for prov in ("nara", "archive-org", "archive-solr", "gutendex", "wayback"):
        assert fp._initial_review_status(prov, fp.TEXTUAL, fp.HIT) is None


def test_enrichment_textual_providers_not_enqueued():
    for prov in ("wikipedia", "grokipedia", "wikimedia-commons", "openserp"):
        assert fp._initial_review_status(prov, fp.TEXTUAL, fp.HIT) is None


def test_non_hit_primary_source_not_enqueued():
    # a miss/error from an enqueue provider is NOT a reviewable find
    assert fp._initial_review_status("openserp-find", fp.TEXTUAL, fp.MISS) is None
    assert fp._initial_review_status("wayback", fp.TEXTUAL, fp.ERROR) is None


def test_structured_never_enqueued():
    assert fp._initial_review_status("nominatim", fp.STRUCTURED, fp.HIT) is None


def test_recorded_event_review_status_follows_policy(_local):
    # nara textual hit -> pending; wikipedia textual hit -> None
    fp.record_fetch_event(
        provider="openserp-find",
        endpoint="https://catalog.archives.gov/x",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
    )
    fp.record_fetch_event(
        provider="wikipedia",
        endpoint="https://en.wikipedia.org/x",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
    )
    assert _events(_local, "openserp-find")[0]["review_status"] == fp.PENDING
    assert _events(_local, "wikipedia")[0]["review_status"] is None
    # Only the nara find surfaces as pending for review.
    pending = fetch_review.list_pending()
    assert len(pending) == 1 and pending[0]["provider"] == "openserp-find"


def test_reject_persists_status(_local):
    fid = fp.record_fetch_event(
        provider="openserp-find",
        endpoint="https://catalog.archives.gov/x",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
    )
    assert fetch_review.list_pending()  # one pending
    ok = fetch_review.reject_fetch_find(
        {"fetch_event_id": fid, "provider": "openserp-find"}
    )
    assert ok is True
    assert fetch_review.list_pending() == []  # no longer pending
    assert _events(_local, "openserp-find")[0]["review_status"] == fp.REJECTED


def test_affirm_persists_affirmed_status(_local):
    fid = fp.record_fetch_event(
        provider="openserp-find",
        endpoint="https://archive.org/wayback/available",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
        result_summary="snapshot 1996 -> https://web.archive.org/web/1996/http://x",
    )
    event = next(e for e in fetch_review.list_pending() if e["fetch_event_id"] == fid)
    assert fetch_review.affirm_fetch_find(event) == f"fetchfind-{fid}"
    assert _events(_local, "openserp-find")[0]["review_status"] == fp.AFFIRMED
    assert fetch_review.list_pending() == []


def test_set_review_status_preserves_sibling_records(_local):
    """Rewriting one event's review_status in a multi-record JSONL must leave the OTHER records
    (and total line count) intact — no corruption/loss."""
    # Three nara textual hits in one provider file.
    fids = [
        fp.record_fetch_event(
            provider="openserp-find",
            endpoint=f"https://catalog.archives.gov/{i}",
            kind=fp.TEXTUAL,
            outcome=fp.HIT,
        )
        for i in range(3)
    ]
    assert len(_events(_local, "openserp-find")) == 3
    # Reject only the middle one.
    assert fetch_review.reject_fetch_find(
        {"fetch_event_id": fids[1], "provider": "openserp-find"}
    )
    evs = {e["fetch_event_id"]: e for e in _events(_local, "openserp-find")}
    assert len(evs) == 3  # none lost
    assert evs[fids[0]]["review_status"] == fp.PENDING  # sibling untouched
    assert evs[fids[1]]["review_status"] == fp.REJECTED  # target updated
    assert evs[fids[2]]["review_status"] == fp.PENDING  # sibling untouched


def test_an_affirm_without_an_id_stores_nothing(_local, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert fetch_review.affirm_fetch_find({"provider": "openserp-find"}) is None
    assert "affirm_not_stored" in caplog.text
