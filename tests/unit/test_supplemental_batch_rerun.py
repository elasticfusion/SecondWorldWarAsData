"""Blocker B: batch-collect must NOT mark supplemental processed (else re-run skips)."""

from unittest.mock import MagicMock, patch

import pytest

from src.grok_client import BatchModeCollecting


def test_extract_with_retry_reraises_batch_collecting():
    """BatchModeCollecting must propagate (not be swallowed as a failure)."""
    from src.extraction.supplemental import _extract_with_retry

    gc = MagicMock()
    gc.extract_json.side_effect = BatchModeCollecting("collecting")
    with patch("src.extraction.supplemental.get_system_prompt", return_value="sys"):
        with pytest.raises(BatchModeCollecting):
            _extract_with_retry("prompt", "01SE", gc)
    # Must not retry a batch-collect (single call), it's not a transient failure.
    assert gc.extract_json.call_count == 1


def test_collect_pass_does_not_mark_processed(tmp_path, monkeypatch):
    """In the collect pass, _extract_supplemental must leave the event UNPROCESSED
    so the batch re-run reprocesses it and writes bibliography."""
    import phase2_extract as p2
    from src.extraction import supplemental as supp

    # Minimal event + parsed so extract_supplemental builds a prompt.
    book = tmp_path / "content" / "RoerDams"
    book.mkdir(parents=True)
    (book / "chapter2a-parsed.json").write_text(
        '{"footnotes": [{"number":1,"url":"u","text":"Cite."}]}'
    )
    ev = {
        "Event": {
            "EventID": "01EV",
            "Event_Name": "X",
            "Sub-events": [
                {
                    "Sub-eventID": "01SE",
                    "Sub-event_summary": "s",
                    "Sub-event_fulltext": {"p": "t"},
                    "Footnote_References": [1],
                    "Endnote_References": [],
                }
            ],
        }
    }
    import json as _j

    (book / "chapter2a-event.json").write_text(_j.dumps(ev))
    for d in ("people", "people_groups", "bibliography", "supplemental"):
        (tmp_path / d).mkdir(exist_ok=True)

    gc = MagicMock()
    gc.extract_json.side_effect = BatchModeCollecting("collecting")  # collect pass
    gc.chat_completion.side_effect = BatchModeCollecting("collecting")
    paths = {"output_root": tmp_path}
    cfg = {"supplemental_material": {"enabled": True, "enrich_with_searches": False}}
    import logging

    p2._extract_supplemental(
        book / "chapter2a-event.json", gc, tmp_path, cfg, logging.getLogger()
    )

    # The event must NOT be marked processed during collection.
    assert not p2._is_processed(tmp_path, "supplemental", "chapter2a-event.json")
