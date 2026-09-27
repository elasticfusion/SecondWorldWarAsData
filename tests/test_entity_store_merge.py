"""Tests for M5 shared-entity-store hardening (spec §3.3).

The correctness crux: concurrent books writing the same entity (e.g. Eisenhower)
must merge event_mentions, not clobber. merge_entity uses optimistic concurrency
(version-conditional put + retry) and is idempotent (re-merging the same mentions
is a no-op).
"""

import json
from unittest.mock import MagicMock

from src.utils.entity_store import DynamoEntityStore


class _CondFail(Exception):
    pass


_CondFail.__name__ = "ConditionalCheckFailedException"


def _store_with_table(table):
    s = DynamoEntityStore.__new__(DynamoEntityStore)
    s._table_name = "t"
    s._region = "us-east-1"
    s._table = table
    # Wire the exception type the code catches.
    table.meta.client.exceptions.ConditionalCheckFailedException = _CondFail
    return s


def _mention(sub_id, book="A"):
    return {"Sub_eventID": sub_id, "book": book}


def test_merge_dedups_mentions():
    s = DynamoEntityStore.__new__(DynamoEntityStore)
    existing = [_mention("s1"), _mention("s2")]
    incoming = [_mention("s2"), _mention("s3")]  # s2 dup, s3 new
    merged = s._merge_mentions(existing, incoming)
    ids = [m["Sub_eventID"] for m in merged]
    assert ids == ["s1", "s2", "s3"]


def test_merge_dedup_respects_book():
    s = DynamoEntityStore.__new__(DynamoEntityStore)
    existing = [_mention("s1", "A")]
    incoming = [_mention("s1", "B")]  # same sub-event, different book => kept
    merged = s._merge_mentions(existing, incoming)
    assert len(merged) == 2


def test_merge_entity_appends_new_mention():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "data": json.dumps({"event_mentions": [_mention("s1")]}),
            "_version": 3,
        }
    }
    s = _store_with_table(table)
    ok = s.merge_entity("people", "E1", {"event_mentions": [_mention("s2")]})
    assert ok is True
    # Conditional put on the current version, bumped to 4.
    kwargs = table.put_item.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":cur"] == 3
    assert kwargs["Item"]["_version"] == 4
    written = json.loads(kwargs["Item"]["data"])
    assert {m["Sub_eventID"] for m in written["event_mentions"]} == {"s1", "s2"}


def test_merge_entity_idempotent_noop():
    """Re-merging an already-present mention writes nothing."""
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "data": json.dumps({"event_mentions": [_mention("s1")]}),
            "_version": 1,
        }
    }
    s = _store_with_table(table)
    ok = s.merge_entity("people", "E1", {"event_mentions": [_mention("s1")]})
    assert ok is True
    table.put_item.assert_not_called()  # no new mentions => no write


def test_merge_entity_retries_on_version_conflict():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "data": json.dumps({"event_mentions": [_mention("s1")]}),
            "_version": 1,
        }
    }
    s = _store_with_table(table)
    # First put conflicts, second succeeds.
    table.put_item.side_effect = [_CondFail(), None]
    ok = s.merge_entity("people", "E1", {"event_mentions": [_mention("s2")]})
    assert ok is True
    assert table.put_item.call_count == 2


def test_merge_entity_first_write_conditional_create():
    table = MagicMock()
    table.get_item.return_value = {}  # entity absent
    s = _store_with_table(table)
    ok = s.merge_entity("people", "E1", {"event_mentions": [_mention("s1")]})
    assert ok is True
    kwargs = table.put_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "attribute_not_exists(cache_key)"
    assert kwargs["Item"]["_version"] == 1


def test_merge_entity_exhausts_retries_returns_false():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {
            "data": json.dumps({"event_mentions": [_mention("s1")]}),
            "_version": 1,
        }
    }
    s = _store_with_table(table)
    table.put_item.side_effect = _CondFail()  # always conflict
    ok = s.merge_entity(
        "people", "E1", {"event_mentions": [_mention("s2")]}, max_retries=3
    )
    assert ok is False
    assert table.put_item.call_count == 3
