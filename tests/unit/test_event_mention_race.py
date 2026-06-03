"""Test that event mention appends are thread-safe under concurrent access.

Verifies the locked_json fix prevents data loss when multiple chapters
mention the same entity simultaneously.
"""

import json
import threading

import pytest


@pytest.fixture
def entity_file(tmp_path):
    """Create a minimal entity file with empty event_mentions."""
    f = tmp_path / "test_entity.json"
    f.write_text(
        json.dumps(
            {
                "PlaceID": "01TEST",
                "current_name": "Normandy",
                "event_mentions": [],
            }
        )
    )
    return f


def test_concurrent_event_mention_append_no_data_loss(entity_file):
    """Simulate 10 concurrent chapters adding mentions to the same entity file."""
    from src.utils.file_lock import locked_json

    num_threads = 10
    errors = []

    def add_mention(thread_id):
        try:
            with locked_json(entity_file) as (data, save):
                mentions = data.get("event_mentions", [])
                # Check for duplicate
                if any(m.get("Sub_eventID") == f"SE_{thread_id}" for m in mentions):
                    return
                mentions.append(
                    {
                        "MentionID": f"M_{thread_id}",
                        "Sub_eventID": f"SE_{thread_id}",
                        "Event_Name": f"Event {thread_id}",
                    }
                )
                data["event_mentions"] = mentions
                save(data)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=add_mention, args=(i,)) for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"

    # Verify all mentions were preserved
    with open(entity_file, encoding="utf-8") as f:
        result = json.load(f)
    assert len(result["event_mentions"]) == num_threads, (
        f"Expected {num_threads} mentions, got {len(result['event_mentions'])}. "
        f"Data loss detected!"
    )


def test_concurrent_append_dedup_prevents_duplicates(entity_file):
    """Multiple threads adding the SAME sub_event_id should result in only one mention."""
    from src.utils.file_lock import locked_json

    num_threads = 5

    def add_same_mention(_):
        with locked_json(entity_file) as (data, save):
            mentions = data.get("event_mentions", [])
            if any(m.get("Sub_eventID") == "SE_SAME" for m in mentions):
                return
            mentions.append(
                {
                    "MentionID": "M_SAME",
                    "Sub_eventID": "SE_SAME",
                    "Event_Name": "Same Event",
                }
            )
            data["event_mentions"] = mentions
            save(data)

    threads = [
        threading.Thread(target=add_same_mention, args=(i,)) for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(entity_file, encoding="utf-8") as f:
        result = json.load(f)
    assert (
        len(result["event_mentions"]) == 1
    ), f"Expected 1 mention (dedup), got {len(result['event_mentions'])}"


def test_locked_json_creates_file_if_missing(tmp_path):
    """locked_json should handle non-existent files gracefully."""
    from src.utils.file_lock import locked_json

    new_file = tmp_path / "new_entity.json"
    with locked_json(new_file) as (data, save):
        data["event_mentions"] = [{"MentionID": "first"}]
        save(data)

    with open(new_file, encoding="utf-8") as f:
        result = json.load(f)
    assert result["event_mentions"] == [{"MentionID": "first"}]


def test_validate_entity_warns_on_missing_fields(tmp_path, caplog):
    """Validation logs warning when required fields are missing."""
    import logging
    from src.utils.file_lock import write_json_with_lock

    people_dir = tmp_path / "people"
    people_dir.mkdir()
    filepath = people_dir / "test_person.json"

    with caplog.at_level(logging.WARNING):
        write_json_with_lock(filepath, {"name": "Test"})  # Missing PersonID

    assert any("missing required fields" in r.message for r in caplog.records)
    assert "PersonID" in caplog.text


def test_validate_entity_passes_valid_data(tmp_path, caplog):
    """Validation does not warn when all required fields present."""
    import logging
    from src.utils.file_lock import write_json_with_lock

    people_dir = tmp_path / "people"
    people_dir.mkdir()
    filepath = people_dir / "test_person.json"

    with caplog.at_level(logging.WARNING):
        write_json_with_lock(filepath, {"PersonID": "01TEST", "name": "Test"})

    assert not any("missing required fields" in r.message for r in caplog.records)
