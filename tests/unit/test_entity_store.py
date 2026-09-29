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
    """G3 + operator spec: Dynamo-backed bibliography dedup — title-focused with
    fallback to archive-ref then author; race-safe merge."""

    @staticmethod
    def _builder(bib_id, title, ref="", authors=None):
        return lambda: {
            "BibliographyID": bib_id,
            "title": title,
            "citation": {"title": title, "author": authors or []},
            "archive_reference_number": ref,
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

    @staticmethod
    def _keys(title="", ref="", author=""):
        return {"title": title, "ref": ref, "author": author}

    def test_new_title_creates_entry_and_indexes(self, entity_store):
        m = self._mention("E1", "1")
        rid = entity_store.store_bibliography(
            self._keys(title="operation overlord"),
            "BIB1",
            self._builder("BIB1", "Operation Overlord"),
            m,
            self._exists(m),
        )
        assert rid == "BIB1"
        assert entity_store.get("bibliography", "BIB1")["title"] == "Operation Overlord"
        assert (
            entity_store.get_bibliography_index()["titles"]["operation overlord"]
            == "BIB1"
        )

    def test_same_title_merges_no_new_entry(self, entity_store):
        m1 = self._mention("E1", "1")
        first = entity_store.store_bibliography(
            self._keys(title="cross channel attack"),
            "BIB1",
            self._builder("BIB1", "Cross Channel Attack"),
            m1,
            self._exists(m1),
        )
        m2 = self._mention("E2", "2")
        second = entity_store.store_bibliography(
            self._keys(title="cross channel attack"),
            "BIB2",
            self._builder("BIB2", "Cross Channel Attack"),
            m2,
            self._exists(m2),
        )
        assert first == second == "BIB1"
        assert len(entity_store.get("bibliography", "BIB1")["mentions"]) == 2
        assert entity_store.get("bibliography", "BIB2") is None

    def test_archive_ref_fallback_matches_different_title(self, entity_store):
        """Different title strings but SAME archive reference -> same entry."""
        m1 = self._mention("E1", "1")
        first = entity_store.store_bibliography(
            self._keys(title="after action report 90th div", ref="rg 407 entry 427"),
            "BIB1",
            self._builder(
                "BIB1", "After Action Report 90th Div", ref="RG 407 Entry 427"
            ),
            m1,
            self._exists(m1),
        )
        m2 = self._mention("E2", "2")
        # Title differs enough to miss fuzzy, but archive ref is identical
        second = entity_store.store_bibliography(
            self._keys(title="aar ninetieth infantry", ref="rg 407 entry 427"),
            "BIB2",
            self._builder("BIB2", "AAR Ninetieth Infantry", ref="RG 407 Entry 427"),
            m2,
            self._exists(m2),
        )
        assert first == second == "BIB1"  # matched on archive ref
        assert entity_store.get("bibliography", "BIB2") is None

    def test_author_fallback_matches_different_title(self, entity_store):
        """Different title + no ref, but SAME author -> same entry (weak fallback)."""
        m1 = self._mention("E1", "1")
        first = entity_store.store_bibliography(
            self._keys(title="the lorraine campaign", author="cole hugh m"),
            "BIB1",
            self._builder("BIB1", "The Lorraine Campaign", authors=["Cole, Hugh M"]),
            m1,
            self._exists(m1),
        )
        m2 = self._mention("E2", "2")
        second = entity_store.store_bibliography(
            self._keys(title="lorraine 1944 gpo edition", author="cole hugh m"),
            "BIB2",
            self._builder(
                "BIB2", "Lorraine 1944 GPO Edition", authors=["Cole, Hugh M"]
            ),
            m2,
            self._exists(m2),
        )
        assert first == second == "BIB1"  # matched on author

    def test_duplicate_mention_is_idempotent(self, entity_store):
        m = self._mention("E1", "1")
        for _ in range(2):
            entity_store.store_bibliography(
                self._keys(title="the lorraine campaign"),
                "BIB1",
                self._builder("BIB1", "The Lorraine Campaign"),
                m,
                self._exists(m),
            )
        assert len(entity_store.get("bibliography", "BIB1")["mentions"]) == 1

    def test_distinct_sources_create_separate_entries(self, entity_store):
        m = self._mention("E1", "1")
        a = entity_store.store_bibliography(
            self._keys(title="cross channel attack", ref="rg 407 e1"),
            "BIBA",
            self._builder("BIBA", "Cross Channel Attack", ref="RG 407 E1"),
            m,
            self._exists(m),
        )
        b = entity_store.store_bibliography(
            self._keys(title="the lorraine campaign", ref="rg 407 e2"),
            "BIBB",
            self._builder("BIBB", "The Lorraine Campaign", ref="RG 407 E2"),
            m,
            self._exists(m),
        )
        assert a == "BIBA" and b == "BIBB"
        idx = entity_store.get_bibliography_index()
        assert idx["titles"]["cross channel attack"] == "BIBA"
        assert idx["refs"]["rg 407 e2"] == "BIBB"
