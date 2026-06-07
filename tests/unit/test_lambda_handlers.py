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


class TestHandlerInvocations:
    """Test actual handler invocations with mocked AWS clients."""

    def test_nat_manager_status(self):
        """nat_manager returns status when action=status."""
        from unittest.mock import MagicMock

        with patch("boto3.client") as mock_client:
            ec2 = MagicMock()
            ec2.describe_nat_gateways.return_value = {"NatGateways": []}
            ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
            mock_client.return_value = ec2

            from lambda_handlers.nat_manager import handler

            result = handler({"action": "status"}, None)
            assert "status" in result or "nat" in str(result).lower()

    def test_batch_poller_poll_no_jobs(self):
        """batch_poller returns empty when no pending jobs."""
        from unittest.mock import MagicMock

        with patch("boto3.resource") as mock_resource:
            table = MagicMock()
            table.scan.return_value = {"Items": []}
            mock_resource.return_value.Table.return_value = table

            from lambda_handlers.batch_poller import handler

            result = handler({"action": "poll"}, None)
            assert result is not None

    def test_nat_manager_sns_non_completion(self):
        """nat_manager ignores non-completion SNS messages."""
        from lambda_handlers.nat_manager import handler

        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {"Message": "some random message"},
                }
            ]
        }
        with patch("boto3.client"):
            result = handler(event, None)
            assert result.get("action") == "none"

    def test_openserp_manager_no_tasks(self):
        """openserp_manager handles no running tasks."""
        from unittest.mock import MagicMock

        with patch("boto3.client") as mock_client:
            ecs = MagicMock()
            ec2 = MagicMock()
            ecs.list_tasks.return_value = {"taskArns": []}
            ecs.describe_services.return_value = {"services": [{"desiredCount": 0}]}
            ec2.describe_nat_gateways.return_value = {"NatGateways": []}

            def client_factory(service, **kwargs):
                if service == "ecs":
                    return ecs
                return ec2

            mock_client.side_effect = client_factory

            from lambda_handlers.openserp_manager import handler

            result = handler({}, None)
            assert result is not None
