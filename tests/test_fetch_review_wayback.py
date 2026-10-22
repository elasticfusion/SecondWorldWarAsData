"""Task #4: a Wayback positive find emits a TEXTUAL fetch-provenance event (enqueued for human
review), and an AFFIRMED find promotes into a SourceMetadata — the SAME ingestion door as an
uploaded book (not a direct-write side-channel).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.enrichment import fetch_review
from src.enrichment import wayback
from src.utils import fetch_provenance as fp


@pytest.fixture(autouse=True)
def _local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_table", lambda: None)
    monkeypatch.setattr(fp, "_LOCAL_DIR", tmp_path / "_fetch_events")
    return tmp_path / "_fetch_events"


def _avail_resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def test_positive_snapshot_emits_pending_textual_event(_local_store):
    payload = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": "https://web.archive.org/web/1996/http://x.com",
                "timestamp": "19960101",
            }
        }
    }
    with patch.object(wayback, "_get_with_courtesy", return_value=_avail_resp(payload)):
        snap = wayback.find_snapshot("http://x.com")
    assert snap is not None
    ev = json.loads((_local_store / "wayback.jsonl").read_text().splitlines()[0])
    assert ev["kind"] == fp.TEXTUAL
    assert ev["outcome"] == fp.HIT
    assert ev["review_status"] == fp.PENDING  # enqueued for human review
    assert "19960101" in ev["result_summary"]


def test_no_snapshot_emits_miss(_local_store):
    with patch.object(
        wayback,
        "_get_with_courtesy",
        return_value=_avail_resp({"archived_snapshots": {}}),
    ):
        assert wayback.find_snapshot("http://x.com") is None
    ev = json.loads((_local_store / "wayback.jsonl").read_text().splitlines()[0])
    assert ev["outcome"] == fp.MISS
    assert ev["kind"] == fp.TEXTUAL


def test_list_pending_surfaces_textual_finds(_local_store):
    fp.record_fetch_event(
        provider="wayback",
        endpoint="https://archive.org/wayback/available",
        kind=fp.TEXTUAL,
        outcome=fp.HIT,
        result_summary="snapshot 1996 -> https://web.archive.org/web/1996/http://x.com",
    )
    pending = fetch_review.list_pending()
    assert len(pending) == 1
    assert pending[0]["review_status"] == fp.PENDING


def test_affirm_promotes_to_source_metadata(_local_store, tmp_path):
    """Affirming a textual find yields a SourceMetadata staged for ingestion (book door)."""
    quarantine = tmp_path / "quarantined.html"
    quarantine.write_text("<html>archived article</html>")
    event = {
        "fetch_event_id": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "provider": "wayback",
        "endpoint": "https://archive.org/wayback/available",
        "queried_at": "2026-10-22T00:00:00+00:00",
        "result_summary": "snapshot 1996 -> https://web.archive.org/web/1996/http://x.com",
        "kind": fp.TEXTUAL,
        "review_status": fp.AFFIRMED,
    }
    src = fetch_review.affirm_fetch_find(event, quarantine, media_type="html")
    assert src is not None
    assert src.source_id == "fetchfind-01HX7YZABCDEFGHJKMNPQRSTVW"
    assert src.original_path == quarantine
    assert src.acquisition_method == "web_archive_affirmed"
    # Acquisition provenance = the archive snapshot URL (traceable back to the fetch event).
    assert src.acquisition_url == "https://web.archive.org/web/1996/http://x.com"
    assert src.media_type == "html"


def test_availability_api_failure_emits_error_event(_local_store):
    """A wayback availability-API transport/non-200 failure emits an error-outcome event so the
    API misbehavior is walk-backable."""
    bad = MagicMock()
    bad.status_code = 503
    with patch.object(wayback, "_get_with_courtesy", return_value=bad):
        assert wayback.find_snapshot("http://x.com") is None
    ev = json.loads((_local_store / "wayback.jsonl").read_text().splitlines()[0])
    assert ev["outcome"] == fp.ERROR
    assert ev["kind"] == fp.TEXTUAL
    assert "503" in (ev["error"] or "")
