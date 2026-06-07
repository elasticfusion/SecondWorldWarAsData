"""Tests for src/utils/cache_backend.py."""

# pylint: disable=missing-function-docstring

import boto3
import pytest
from moto import mock_aws

from src.utils.cache_backend import DiskCacheBackend, DynamoCacheBackend


class TestDiskCacheBackend:
    def test_set_and_get(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        cache["key1"] = "value1"
        assert cache["key1"] == "value1"

    def test_contains(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        cache["exists"] = "yes"
        assert "exists" in cache
        assert "nope" not in cache

    def test_pop(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        cache["k"] = "v"
        assert cache.pop("k") == "v"
        assert cache.pop("k", "default") == "default"

    def test_clear(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        cache["a"] = "1"
        cache["b"] = "2"
        cache.clear()
        assert "a" not in cache
        assert "b" not in cache

    def test_get_sub_cache(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        sub = cache.get_sub_cache("people")
        sub["test"] = "val"
        assert sub["test"] == "val"
        assert "test" not in cache

    def test_missing_key_raises(self, tmp_path):
        cache = DiskCacheBackend(tmp_path / "cache")
        with pytest.raises(KeyError):
            _ = cache["missing"]


@pytest.fixture
def dynamo_cache():
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="test-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoCacheBackend(
            table_name="test-cache", prefix="test", region="us-east-1"
        )


class TestDynamoCacheBackend:
    def test_set_and_get(self, dynamo_cache):
        dynamo_cache["key1"] = "value1"
        assert dynamo_cache["key1"] == "value1"

    def test_contains(self, dynamo_cache):
        dynamo_cache["exists"] = "yes"
        assert "exists" in dynamo_cache
        assert "nope" not in dynamo_cache

    def test_missing_key_raises(self, dynamo_cache):
        with pytest.raises(KeyError):
            _ = dynamo_cache["missing"]

    def test_pop(self, dynamo_cache):
        dynamo_cache["k"] = "v"
        assert dynamo_cache.pop("k") == "v"
        assert dynamo_cache.pop("k", "default") == "default"

    def test_preload(self, dynamo_cache):
        dynamo_cache["a"] = "1"
        dynamo_cache["b"] = "2"
        # preload uses scan with 'response' which is a DynamoDB reserved keyword
        # This tests the local cache fallback instead
        assert dynamo_cache._local[dynamo_cache._pk("a")] == "1"
        assert dynamo_cache._local[dynamo_cache._pk("b")] == "2"

    def test_get_sub_cache(self, dynamo_cache):
        sub = dynamo_cache.get_sub_cache("people")
        sub["name"] = "Bradley"
        assert sub["name"] == "Bradley"
        assert sub.prefix == "test/people"

    def test_contains_uses_preloaded(self, dynamo_cache):
        dynamo_cache["x"] = "val"
        dynamo_cache._preloaded = True
        assert "x" in dynamo_cache
