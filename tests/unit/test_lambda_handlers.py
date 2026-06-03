"""Tests for Lambda handlers (trigger, nat_manager, and smaller handlers)."""

# pylint: disable=missing-function-docstring,import-error,unused-argument

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def set_env():
    env = {
        "ECS_CLUSTER": "test-cluster",
        "PRIVATE_SUBNET_IDS": "subnet-1,subnet-2",
        "SECURITY_GROUP_ID": "sg-123",
        "S3_BUCKET": "test-bucket",
        "CACHE_TABLE": "test-table",
        "ENV_NAME": "test",
        "NOTIFICATION_TOPIC_ARN": "",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
    }
    with patch.dict(os.environ, env):
        yield


class TestDedupGateHandler:
    def test_blocks_when_review_incomplete(self):
        with patch.dict(os.environ, {"S3_BUCKET": ""}):
            from lambda_handlers.dedup_gate_handler import handler

            result = handler({"Records": []}, None)
            assert result["action"] == "blocked"


class TestSmallHandlers:
    """Verify all handlers are importable and callable."""

    def test_import_handler(self):
        from lambda_handlers.import_handler import handler

        assert callable(handler)

    def test_openserp_manager(self):
        from lambda_handlers.openserp_manager import handler

        assert callable(handler)

    def test_phase1_handler(self):
        from lambda_handlers.phase1_handler import handler

        assert callable(handler)

    def test_phase2_handler(self):
        from lambda_handlers.phase2_handler import handler

        assert callable(handler)

    def test_phase3_handler(self):
        from lambda_handlers.phase3_handler import handler

        assert callable(handler)

    def test_nat_manager(self):
        from lambda_handlers.nat_manager import handler

        assert callable(handler)
