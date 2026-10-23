"""Enqueue POLICY: only citable primary-source TEXTUAL providers (wayback/archive-org/archive-solr/
gutendex/nara) are enqueued for human review; encyclopedia/enrichment textual lookups (wikipedia,
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
    for prov in ("wayback", "archive-org", "archive-solr", "gutendex", "nara"):
        assert fp._initial_review_status(prov, fp.TEXTUAL, fp.HIT) == fp.PENDING


def test_enrichment_textual_providers_not_enqueued():
    for prov in ("wikipedia", "grokipedia", "wikimedia-commons", "openserp"):
        assert fp._initial_review_status(prov, fp.TEXTUAL, fp.HIT) is None


def test_non_hit_primary_source_not_enqueued():
    # a miss/error from an enqueue provider is NOT a reviewable find
    assert fp._initial_review_status("nara", fp.TEXTUAL, fp.MISS) is None
    assert fp._initial_review_status("wayback", fp.TEXTUAL, fp.ERROR) is None


def test_structured_never_enqueued():
    assert fp._initial_review_status("nominatim", fp.STRUCTURED, fp.HIT) is None


def test_recorded_event_review_status_follows_policy(_local):
    # nara textual hit -> pending; wikipedia textual hit -> None
    fp.record_fetch_event(
        provider="nara",
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
    assert _events(_local, "nara")[0]["review_status"] == fp.PENDING
    assert _events(_local, "wikipedia")[0]["review_status"] is None
    # Only the nara find surfaces as pending for review.
    pending = fetch_review.list_pending()
    assert len(pending) == 1 and pending[0]["provider"] == "nara"


def test_reject_persists_status(_local):
    fid = fp.record_fetch_event(
        provider="nara",
        endpoint="https://catalog.archives.gov/x",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
    )
    assert fetch_review.list_pending()  # one pending
    ok = fetch_review.reject_fetch_find({"fetch_event_id": fid, "provider": "nara"})
    assert ok is True
    assert fetch_review.list_pending() == []  # no longer pending
    assert _events(_local, "nara")[0]["review_status"] == fp.REJECTED


def test_affirm_persists_affirmed_status(_local, tmp_path):
    q = tmp_path / "doc.html"
    q.write_text("<html>archived</html>")
    fid = fp.record_fetch_event(
        provider="wayback",
        endpoint="https://archive.org/wayback/available",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
        result_summary="snapshot 1996 -> https://web.archive.org/web/1996/http://x",
    )
    event = next(e for e in fetch_review.list_pending() if e["fetch_event_id"] == fid)
    src = fetch_review.affirm_fetch_find(event, q)
    assert src is not None
    assert _events(_local, "wayback")[0]["review_status"] == fp.AFFIRMED
    assert fetch_review.list_pending() == []


def test_set_review_status_preserves_sibling_records(_local):
    """Rewriting one event's review_status in a multi-record JSONL must leave the OTHER records
    (and total line count) intact — no corruption/loss."""
    # Three nara textual hits in one provider file.
    fids = [
        fp.record_fetch_event(
            provider="nara",
            endpoint=f"https://catalog.archives.gov/{i}",
            kind=fp.TEXTUAL,
            outcome=fp.HIT,
        )
        for i in range(3)
    ]
    assert len(_events(_local, "nara")) == 3
    # Reject only the middle one.
    assert fetch_review.reject_fetch_find(
        {"fetch_event_id": fids[1], "provider": "nara"}
    )
    evs = {e["fetch_event_id"]: e for e in _events(_local, "nara")}
    assert len(evs) == 3  # none lost
    assert evs[fids[0]]["review_status"] == fp.PENDING  # sibling untouched
    assert evs[fids[1]]["review_status"] == fp.REJECTED  # target updated
    assert evs[fids[2]]["review_status"] == fp.PENDING  # sibling untouched
