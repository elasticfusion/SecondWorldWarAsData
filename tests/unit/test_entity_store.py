"""Tests for DynamoEntityStore."""

# pylint: disable=missing-function-docstring

import boto3
import pytest
from moto import mock_aws

from src.utils.entity_store import DynamoEntityStore


@pytest.fixture
def entity_store():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoEntityStore(table_name="test-table", region="us-east-1")


class TestDynamoEntityStore:
    def test_put_and_get(self, entity_store):
        data = {"PersonID": "01TEST", "name": "Eisenhower", "event_mentions": []}
        entity_store.put("people", "01TEST", data)
        result = entity_store.get("people", "01TEST")
        assert result["PersonID"] == "01TEST"
        assert result["name"] == "Eisenhower"

    def test_get_nonexistent(self, entity_store):
        assert entity_store.get("people", "NOPE") is None

    def test_delete(self, entity_store):
        entity_store.put("places", "01PL", {"PlaceID": "01PL", "current_name": "Nancy"})
        entity_store.delete("places", "01PL")
        assert entity_store.get("places", "01PL") is None

    def test_query_unenriched(self, entity_store):
        entity_store.put("people", "01A", {"PersonID": "01A", "name": "A"})
        entity_store.put(
            "people",
            "01B",
            {"PersonID": "01B", "name": "B", "enrichment_status": "enriched"},
        )
        entity_store.put("people", "01C", {"PersonID": "01C", "name": "C"})

        results = entity_store.query_unenriched("people")
        ids = [r["PersonID"] for r in results]
        assert "01A" in ids
        assert "01C" in ids
        assert "01B" not in ids  # Already enriched

    def test_count(self, entity_store):
        entity_store.put(
            "equipment", "01E1", {"EquipmentID": "01E1", "common_name": "Sherman"}
        )
        entity_store.put(
            "equipment", "01E2", {"EquipmentID": "01E2", "common_name": "Tiger"}
        )
        assert entity_store.count("equipment") == 2
        assert entity_store.count("people") == 0

    def test_query_by_name(self, entity_store):
        entity_store.put("people", "01A", {"PersonID": "01A", "name": "Eisenhower"})
        entity_store.put("people", "01B", {"PersonID": "01B", "name": "Bradley"})

        result = entity_store.query_by_name("people", "eisenhower")
        assert result is not None
        assert result["PersonID"] == "01A"

        assert entity_store.query_by_name("people", "patton") is None


class TestStoreBibliography:
    """G3/Option B: Dynamo-backed bibliography title-dedup + race-safe merge."""

    @staticmethod
    def _builder(bib_id, title):
        return lambda: {
            "BibliographyID": bib_id,
            "title": title,
            "citation": {"title": title},
            "mentions": [],
        }

    @staticmethod
    def _mention(event_id, ref_no):
        return {"EventID": event_id, "Sub-eventID": "", "reference_number": ref_no}

    @staticmethod
    def _exists(m):
        return lambda mentions: any(
            x.get("EventID") == m["EventID"]
            and x.get("reference_number") == m["reference_number"]
            for x in mentions
        )

    def test_new_title_creates_entry_and_indexes(self, entity_store):
        m = self._mention("E1", "1")
        rid = entity_store.store_bibliography(
            "operation overlord",
            "BIB1",
            self._builder("BIB1", "Operation Overlord"),
            m,
            self._exists(m),
        )
        assert rid == "BIB1"
        assert entity_store.get("bibliography", "BIB1")["title"] == "Operation Overlord"
        assert entity_store.get_bibliography_index()["operation overlord"] == "BIB1"

    def test_same_title_merges_into_existing_no_new_entry(self, entity_store):
        m1 = self._mention("E1", "1")
        first = entity_store.store_bibliography(
            "cross channel attack",
            "BIB1",
            self._builder("BIB1", "Cross Channel Attack"),
            m1,
            self._exists(m1),
        )
        m2 = self._mention("E2", "2")
        second = entity_store.store_bibliography(
            "cross channel attack",
            "BIB2",  # different proposed id
            self._builder("BIB2", "Cross Channel Attack"),
            m2,
            self._exists(m2),
        )
        assert first == second == "BIB1"  # deduped to the first entry
        entry = entity_store.get("bibliography", "BIB1")
        assert len(entry["mentions"]) == 2  # both mentions merged
        assert entity_store.get("bibliography", "BIB2") is None  # no 2nd entry

    def test_duplicate_mention_is_idempotent(self, entity_store):
        m = self._mention("E1", "1")
        entity_store.store_bibliography(
            "the lorraine campaign",
            "BIB1",
            self._builder("BIB1", "The Lorraine Campaign"),
            m,
            self._exists(m),
        )
        # same mention again (retry / duplicate event)
        entity_store.store_bibliography(
            "the lorraine campaign",
            "BIB1",
            self._builder("BIB1", "The Lorraine Campaign"),
            m,
            self._exists(m),
        )
        assert len(entity_store.get("bibliography", "BIB1")["mentions"]) == 1

    def test_different_titles_create_separate_entries(self, entity_store):
        m = self._mention("E1", "1")
        a = entity_store.store_bibliography(
            "cross channel attack",
            "BIBA",
            self._builder("BIBA", "Cross Channel Attack"),
            m,
            self._exists(m),
        )
        b = entity_store.store_bibliography(
            "the lorraine campaign",
            "BIBB",
            self._builder("BIBB", "The Lorraine Campaign"),
            m,
            self._exists(m),
        )
        assert a == "BIBA" and b == "BIBB"
        idx = entity_store.get_bibliography_index()
        assert idx["cross channel attack"] == "BIBA"
        assert idx["the lorraine campaign"] == "BIBB"
