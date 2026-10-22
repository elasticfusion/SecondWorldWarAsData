"""§1.0d WARNING regression: dedup merge must be CRASH-ATOMIC — the merged primary is written +
validated BEFORE the secondary is deleted. If the guarded primary write is BLOCKED (schema-invalid
merged result) or crashes, the secondary MUST survive intact (its mentions are not lost) and the
merge reports not-merged. Previously the secondary was unlinked before the end-of-loop primary
write, so a blocked/crashed write destroyed the secondary's data permanently.
"""

import json
from unittest.mock import patch

from src.dedup import merge as m


def test_people_merge_blocked_write_keeps_secondary(tmp_path):
    people = tmp_path / "people"
    people.mkdir()
    (people / "index.json").write_text("{}")
    (people / "a.json").write_text(
        json.dumps({"PersonID": "01" + "A" * 24, "name": "A", "event_mentions": []})
    )
    (people / "b.json").write_text(
        json.dumps({"PersonID": "01" + "B" * 24, "name": "A.", "event_mentions": []})
    )
    entries = [
        {"filename": "a.json", "name": "A"},
        {"filename": "b.json", "name": "A."},
    ]
    # Force the guarded write to BLOCK (as if the merged record were schema-invalid).
    with patch.object(m, "_write_entity_guarded", return_value=False):
        m.do_merge(people, entries, 0)
    # The secondary must still exist — no data loss on a blocked write.
    assert (
        people / "b.json"
    ).exists(), "secondary deleted despite a blocked primary write"


def test_generic_merge_blocked_write_keeps_secondary(tmp_path):
    places = tmp_path / "places"
    places.mkdir()
    (places / "p1.json").write_text(
        json.dumps({"PlaceID": "01" + "P" * 24, "name": "P1", "event_mentions": []})
    )
    (places / "p2.json").write_text(
        json.dumps({"PlaceID": "01" + "Q" * 24, "name": "P2", "event_mentions": []})
    )
    entries = [
        {"filename": "p1.json", "name": "P1"},
        {"filename": "p2.json", "name": "P2"},
    ]
    with patch.object(m, "_write_entity_guarded", return_value=False):
        m.merge_generic(places, entries, 0, id_field="PlaceID")
    assert (
        places / "p2.json"
    ).exists(), "secondary deleted despite a blocked primary write"


def test_people_merge_success_still_merges_and_deletes(tmp_path):
    """Sanity: with a normal (passing) write, the merge still completes (secondary removed)."""
    people = tmp_path / "people"
    people.mkdir()
    (people / "index.json").write_text("{}")
    (people / "a.json").write_text(
        json.dumps({"PersonID": "01" + "A" * 24, "name": "A", "event_mentions": []})
    )
    (people / "b.json").write_text(
        json.dumps(
            {
                "PersonID": "01" + "B" * 24,
                "name": "A.",
                "event_mentions": [{"MentionID": "01M", "Sub_eventID": "01S"}],
            }
        )
    )
    entries = [
        {"filename": "a.json", "name": "A"},
        {"filename": "b.json", "name": "A."},
    ]
    result = m.do_merge(people, entries, 0)
    assert result == "A"
    assert not (people / "b.json").exists()  # secondary merged + removed
    merged = json.loads((people / "a.json").read_text())
    assert len(merged["event_mentions"]) == 1  # secondary's mention carried over


def test_generic_merge_mixed_block_then_success_no_double_count(tmp_path):
    """Primary + 2 secondaries; the FIRST secondary's write BLOCKS, the SECOND SUCCEEDS. The
    persisted primary must NOT contain the blocked secondary's mentions (no cross-iteration
    in-memory pollution), and the blocked secondary file must still exist. The second secondary's
    mention IS present (it merged cleanly)."""
    places = tmp_path / "places"
    places.mkdir()
    (places / "p0.json").write_text(
        json.dumps({"PlaceID": "01" + "0" * 24, "name": "P0", "event_mentions": []})
    )
    (places / "p1.json").write_text(
        json.dumps(
            {
                "PlaceID": "01" + "1" * 24,
                "name": "P1",
                "event_mentions": [
                    {
                        "MentionID": "01M1AAAAAAAAAAAAAAAAAAAAAA",
                        "Sub_eventID": "01S1AAAAAAAAAAAAAAAAAAAAAA",
                    }
                ],
            }
        )
    )
    (places / "p2.json").write_text(
        json.dumps(
            {
                "PlaceID": "01" + "2" * 24,
                "name": "P2",
                "event_mentions": [
                    {
                        "MentionID": "01M2BBBBBBBBBBBBBBBBBBBBBB",
                        "Sub_eventID": "01S2BBBBBBBBBBBBBBBBBBBBBB",
                    }
                ],
            }
        )
    )
    entries = [
        {"filename": "p0.json", "name": "P0"},
        {"filename": "p1.json", "name": "P1"},  # secondary 1 -> write BLOCKS
        {"filename": "p2.json", "name": "P2"},  # secondary 2 -> write SUCCEEDS
    ]

    real_guard = m._write_entity_guarded

    def guard(path, data):
        # Deterministically BLOCK the write that would persist the blocked secondary's mention
        # (S1), regardless of iteration order; let every other write through.
        sub_ids = {mn.get("Sub_eventID") for mn in data.get("event_mentions", [])}
        if "01S1AAAAAAAAAAAAAAAAAAAAAA" in sub_ids:
            return False
        return real_guard(path, data)

    with patch.object(m, "_write_entity_guarded", side_effect=guard):
        m.merge_generic(places, entries, 0, id_field="PlaceID")

    # p1 (whose write always blocks) must survive; p2 merged cleanly and is removed.
    assert (places / "p1.json").exists(), "blocked secondary was deleted (data loss)"
    assert not (places / "p2.json").exists(), "cleanly-merged secondary not removed"
    primary = json.loads((places / "p0.json").read_text())
    sub_ids = {mn.get("Sub_eventID") for mn in primary.get("event_mentions", [])}
    assert (
        "01S2BBBBBBBBBBBBBBBBBBBBBB" in sub_ids
    ), "successfully-merged secondary's mention missing"
    assert (
        "01S1AAAAAAAAAAAAAAAAAAAAAA" not in sub_ids
    ), "BLOCKED secondary's mention leaked into the primary (double-count)"
