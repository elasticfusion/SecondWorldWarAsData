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
