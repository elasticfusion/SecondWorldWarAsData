"""Tests for S3Storage using moto mock."""

# pylint: disable=missing-function-docstring,import-error

import json

import boto3
import pytest
from moto import mock_aws

from src.utils.storage import S3Storage


@pytest.fixture
def s3_storage():
    """Create a mocked S3 bucket and return S3Storage instance."""
    with mock_aws():
        conn = boto3.client("s3", region_name="us-east-1")
        conn.create_bucket(Bucket="test-bucket")
        yield S3Storage(bucket="test-bucket", region="us-east-1")


class TestS3StorageReadWrite:
    def test_write_and_read_json(self, s3_storage):
        data = {"PersonID": "01TEST", "name": "Eisenhower"}
        s3_storage.write_json("people/eisenhower.json", data)
        result = s3_storage.read_json("people/eisenhower.json")
        assert result == data

    def test_write_and_read_bytes(self, s3_storage):
        content = b"binary content here"
        s3_storage.write_bytes("files/test.bin", content)
        result = s3_storage.read_bytes("files/test.bin")
        assert result == content

    def test_exists_true(self, s3_storage):
        s3_storage.write_json("test.json", {"key": "value"})
        assert s3_storage.exists("test.json") is True

    def test_exists_false(self, s3_storage):
        assert s3_storage.exists("nonexistent.json") is False

    def test_delete(self, s3_storage):
        s3_storage.write_json("to_delete.json", {"x": 1})
        assert s3_storage.exists("to_delete.json") is True
        s3_storage.delete("to_delete.json")
        assert s3_storage.exists("to_delete.json") is False


class TestS3StorageListFiles:
    def test_list_files_json(self, s3_storage):
        s3_storage.write_json("output/people/a.json", {"name": "A"})
        s3_storage.write_json("output/people/b.json", {"name": "B"})
        s3_storage.write_bytes("output/people/c.txt", b"not json")

        files = s3_storage.list_files("output/people", "*.json")
        assert len(files) == 2
        assert "output/people/a.json" in files
        assert "output/people/b.json" in files

    def test_list_files_empty_prefix(self, s3_storage):
        files = s3_storage.list_files("output/empty")
        assert files == []

    def test_list_files_with_pattern(self, s3_storage):
        s3_storage.write_json("reports/report.json", {})
        s3_storage.write_bytes("reports/report.html", b"<html>")

        json_files = s3_storage.list_files("reports", "*.json")
        html_files = s3_storage.list_files("reports", "*.html")
        assert len(json_files) == 1
        assert len(html_files) == 1


class TestS3StorageWithPrefix:
    def test_prefix_routing(self):
        """Storage with prefix prepends it to all keys."""
        with mock_aws():
            conn = boto3.client("s3", region_name="us-east-1")
            conn.create_bucket(Bucket="test-bucket")
            storage = S3Storage(
                bucket="test-bucket", prefix="wwii-data", region="us-east-1"
            )

            storage.write_json("people/test.json", {"name": "Test"})

            # Verify the actual S3 key includes the prefix
            resp = conn.get_object(
                Bucket="test-bucket", Key="wwii-data/people/test.json"
            )
            data = json.loads(resp["Body"].read())
            assert data["name"] == "Test"

            # Read back via storage
            result = storage.read_json("people/test.json")
            assert result["name"] == "Test"


class TestS3StorageErrorHandling:
    def test_read_nonexistent_raises(self, s3_storage):
        with pytest.raises(Exception):
            s3_storage.read_json("does/not/exist.json")
