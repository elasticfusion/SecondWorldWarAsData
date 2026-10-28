"""PR-B: people RECALL-GAP review.

A deterministic rank-anchored name present in the source but NOT resolved to an extracted person
must be surfaced to a needs-review queue (category='recall-gap'), NEVER silently dropped, and must
NOT fail the run. Ambiguous names (e.g. "Private Smith") are surfaced for human follow-up.
"""

import json
import logging

from src.extraction import recall_gap as R


def test_find_rank_anchored_mentions_detects_ranks():
    text = (
        "General Patton advanced. Private Smith was wounded. "
        "Lt. Col. Jones held the line. Field Marshal von Rundstedt commanded."
    )
    mentions = R.find_rank_anchored_mentions(text)
    joined = " | ".join(mentions)
    assert "Patton" in joined
    assert "Smith" in joined  # ambiguous name still surfaced
    assert "Jones" in joined
    assert "Rundstedt" in joined


def test_detect_recall_gaps_flags_unresolved_only():
    text = "General Patton advanced. Private Smith was wounded."
    extracted = [{"name": "George Patton"}]  # Patton resolved, Smith missed
    gaps = R.detect_recall_gaps(text, extracted)
    assert any("Smith" in g for g in gaps)
    assert not any("Patton" in g for g in gaps)


def test_resolution_matches_on_surname_and_aliases():
    text = "Colonel Donitz issued the order."
    extracted = [{"name": "Karl", "aliases": ["Karl Dönitz"]}]
    # alias "Dönitz" normalizes to "donitz" and resolves "Colonel Donitz".
    gaps = R.detect_recall_gaps(text, extracted)
    assert gaps == []


def test_ambiguous_name_is_surfaced_not_dropped(tmp_path, caplog):
    people_dir = tmp_path / "people"
    people_dir.mkdir()
    text = "Private Smith crossed the river."
    with caplog.at_level(logging.WARNING, logger=R.__name__):
        n = R.record_people_recall_gaps(
            people_dir, "E1", "SE1", text, extracted_people=[]
        )
    assert n == 1
    assert "OBSERV people.recall_gap" in caplog.text
    queue = json.loads((people_dir / "review_queue.json").read_text())
    assert len(queue) == 1
    item = queue[0]
    assert item["category"] == "recall-gap"
    assert "Smith" in item["mention"]
    assert item["event_id"] == "E1"
    assert item["sub_event_id"] == "SE1"


def test_recall_gap_appends_to_existing_queue(tmp_path):
    people_dir = tmp_path / "people"
    people_dir.mkdir()
    (people_dir / "review_queue.json").write_text(json.dumps([{"category": "other"}]))
    R.record_people_recall_gaps(
        people_dir, "E2", "SE2", "Major Hughes led the assault.", extracted_people=[]
    )
    queue = json.loads((people_dir / "review_queue.json").read_text())
    assert len(queue) == 2
    assert any(i.get("category") == "recall-gap" for i in queue)


def test_no_gap_writes_nothing(tmp_path):
    people_dir = tmp_path / "people"
    people_dir.mkdir()
    # Patton is extracted -> no gap -> no queue file created.
    n = R.record_people_recall_gaps(
        people_dir, "E3", "SE3", "General Patton advanced.", [{"name": "George Patton"}]
    )
    assert n == 0
    assert not (people_dir / "review_queue.json").exists()


def test_record_recall_gaps_is_fail_safe_on_bad_dir():
    # A non-existent/invalid people_dir must NOT raise (recall-gap must never fail the run).
    n = R.record_people_recall_gaps(
        "/nonexistent/\x00bad", "E4", "SE4", "Captain Reed held.", []
    )
    assert n == 0


def test_detect_recall_gaps_fail_safe_on_non_string():
    assert R.detect_recall_gaps(None, []) == []  # type: ignore[arg-type]
    assert R.find_rank_anchored_mentions(None) == []  # type: ignore[arg-type]
