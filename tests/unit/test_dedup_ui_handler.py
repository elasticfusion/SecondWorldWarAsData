"""Tests for dedup_ui_handler.py Lambda."""

# pylint: disable=missing-function-docstring,import-error

import json
import os
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_storage():
    """Mock S3Storage."""
    storage = Mock()
    storage.read_json = Mock(return_value={"duplicates": []})
    storage.write_json = Mock()
    storage.list_files = Mock(return_value=[])
    storage.delete_file = Mock()
    return storage


@pytest.fixture
def mock_context():
    """Mock Lambda context."""
    ctx = Mock()
    ctx.get_remaining_time_in_millis = Mock(return_value=60000)
    return ctx


@pytest.fixture(autouse=True)
def set_env():
    """Set required environment variables."""
    with patch.dict(
        os.environ, {"S3_BUCKET": "test-bucket", "CACHE_TABLE": "test-table"}
    ):
        yield


class TestPathValidation:
    """Test path traversal prevention."""

    def test_rejects_path_traversal_in_detail(self, mock_context):
        from lambda_handlers.dedup_ui_handler import handler

        event = {
            "httpMethod": "GET",
            "path": "/dedup/api/detail/people/../../secrets/key.json",
        }
        resp = handler(event, mock_context)
        assert resp["statusCode"] == 400
        assert "invalid" in json.loads(resp["body"]).get("error", "")

    def test_rejects_path_traversal_in_search(self, mock_context):
        from lambda_handlers.dedup_ui_handler import handler

        event = {
            "httpMethod": "GET",
            "path": "/dedup/api/search/people/../../../etc/passwd",
        }
        resp = handler(event, mock_context)
        assert resp["statusCode"] == 400


class TestHandleAction:
    """Test action routing and skip/exclude logic."""

    def test_skip_action_returns_200(self, mock_storage):
        from lambda_handlers.dedup_ui_handler import _handle_action

        mock_storage.read_json.return_value = {
            "duplicates": [
                {
                    "people": [
                        {"filename": "a.json", "name": "A"},
                        {"filename": "b.json", "name": "B"},
                    ]
                }
            ]
        }
        event = {
            "body": json.dumps(
                {
                    "action": "skip",
                    "entity_type": "people",
                    "group_index": 0,
                    "group_filenames": ["a.json", "b.json"],
                }
            )
        }
        with patch("lambda_handlers.dedup_ui_handler._record_reviewed"):
            resp = _handle_action(event, mock_storage, {})
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["result"] == "skipped"

    def test_unknown_action_returns_400(self, mock_storage):
        from lambda_handlers.dedup_ui_handler import _handle_action

        event = {"body": json.dumps({"action": "invalid", "entity_type": "people"})}
        resp = _handle_action(event, mock_storage, {})
        assert resp["statusCode"] == 400

    def test_exclude_action(self, mock_storage):
        from lambda_handlers.dedup_ui_handler import _handle_action

        mock_storage.read_json.return_value = {
            "duplicates": [
                {
                    "people": [
                        {"filename": "a.json", "name": "A"},
                        {"filename": "b.json", "name": "B"},
                    ]
                }
            ]
        }
        event = {
            "body": json.dumps(
                {
                    "action": "exclude",
                    "entity_type": "people",
                    "group_index": 0,
                    "group_filenames": ["a.json", "b.json"],
                }
            )
        }
        with patch("src.dedup.exclusions.ExclusionStore") as mock_exc:
            mock_store = Mock()
            mock_exc.return_value = mock_store
            resp = _handle_action(event, mock_storage, {})
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["result"] == "excluded"
        mock_store.add_group.assert_called_once()


class TestHelpers:
    """Test utility functions."""

    def test_report_path(self):
        from lambda_handlers.dedup_ui_handler import _report_path

        assert "people" in _report_path("people")
        assert "places" in _report_path("places")
        assert "equipment" in _report_path("equipment")

    def test_json_response_format(self):
        from lambda_handlers.dedup_ui_handler import _json_response

        resp = _json_response(200, {"result": "ok"})
        assert resp["statusCode"] == 200
        assert resp["headers"]["Content-Type"] == "application/json"
        assert json.loads(resp["body"]) == {"result": "ok"}

    def test_dedupe_groups_removes_duplicates(self):
        from lambda_handlers.dedup_ui_handler import _dedupe_groups

        groups = [
            {
                "people": [
                    {"filename": "a.json", "name": "A"},
                    {"filename": "a.json", "name": "A"},  # duplicate
                    {"filename": "b.json", "name": "B"},
                ]
            }
        ]
        result = _dedupe_groups(groups)
        assert len(result[0]["people"]) == 2

    def test_sort_groups_alphabetically(self):
        from lambda_handlers.dedup_ui_handler import _sort_groups

        groups = [
            {"people": [{"name": "Zebra"}]},
            {"people": [{"name": "Alpha"}]},
            {"people": [{"name": "Middle"}]},
        ]
        result = _sort_groups(groups)
        names = [g["people"][0]["name"] for g in result]
        assert names == ["Alpha", "Middle", "Zebra"]


class TestRemoveGroupFromReport:
    """Test report modification."""

    def test_removes_group_by_index(self, mock_storage):
        from lambda_handlers.dedup_ui_handler import _remove_group_from_report

        mock_storage.read_json.return_value = {
            "duplicates": [
                {"people": [{"filename": "a.json"}]},
                {"people": [{"filename": "b.json"}]},
            ]
        }
        _remove_group_from_report("people", 0, mock_storage)
        # Should write back with first group removed
        written = mock_storage.write_json.call_args[0][1]
        assert len(written["duplicates"]) == 1
        assert written["duplicates"][0]["people"][0]["filename"] == "b.json"

    def test_handles_out_of_range_index(self, mock_storage):
        from lambda_handlers.dedup_ui_handler import _remove_group_from_report

        mock_storage.read_json.return_value = {"duplicates": []}
        # Should not crash
        _remove_group_from_report("people", 99, mock_storage)
