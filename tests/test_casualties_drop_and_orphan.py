"""Casualties follow-ups: (A) a schema-rejected casualty must emit a LOUD OBSERV (not silent-drop);
(B) a casualty anchored via event_context must NOT be false-flagged as an orphan by A3.
"""

import logging
from pathlib import Path
from unittest.mock import patch
import sys

sys.argv = ["x"]
from scripts.validate_phase2 import _iter_mentions


def test_B_event_context_is_valid_provenance_not_orphan():
    rec = {
        "CasualtyID": "01X",
        "type": "casualties",
        "event_context": {
            "EventID": "01EVT",
            "Sub_eventID": "01SUB",
            "book": "XChannel",
        },
    }
    m = _iter_mentions(rec)
    assert len(m) == 1 and m[0]["EventID"] == "01EVT"  # not an orphan


def test_B_true_orphan_still_flagged():
    assert _iter_mentions({"CasualtyID": "01Y", "type": "casualties"}) == []


def test_B_event_mentions_still_works():
    assert len(_iter_mentions({"event_mentions": [{"EventID": "01Z"}]})) == 1


def test_A_rejected_casualty_emits_observ(tmp_path, caplog):
    from src.extraction import casualties as C

    # force the write guard to reject (return False)
    with patch("src.utils.file_lock.write_json_with_lock", return_value=False):
        with caplog.at_level(logging.WARNING):
            C._save_casualty(
                {"CasualtyID": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "type": "killed"}, tmp_path
            )
    assert any(
        "casualties.create_blocked" in r.message for r in caplog.records
    ), "rejected casualty did not emit a loud OBSERV (silent drop?)"


def test_A_successful_write_no_block_observ(tmp_path, caplog):
    from src.extraction import casualties as C

    with patch("src.utils.file_lock.write_json_with_lock", return_value=True):
        with caplog.at_level(logging.WARNING):
            C._save_casualty(
                {"CasualtyID": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "type": "killed"}, tmp_path
            )
    assert not any("create_blocked" in r.message for r in caplog.records)


def test_B_event_context_with_none_eventid_is_orphan():
    """A present event_context whose EventID is None/'null' is NOT real provenance -> still orphan."""
    assert _iter_mentions({"event_context": {"EventID": None}}) == []
    assert _iter_mentions({"event_context": {"EventID": "null"}}) == []
