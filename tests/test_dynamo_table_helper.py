"""DRY P1.3 guard: the DynamoDB table-handle construction that ~20 sites hand-rolled (with the
region resolved two divergent ways) is now ONE helper, aws_clients.get_dynamo_table, whose region
comes from the single source of truth config.get_aws_region(). These tests pin that contract and
prove the rewired Flavor-A _table() helpers route through it (consistent region — no drift).
"""

from unittest import mock

import src.utils.aws_clients as ac


class _FakeTable:
    def __init__(self, name):
        self.name = name


class _FakeResource:
    def __init__(self):
        self.region = None

    def __call__(self, service, region_name=None):
        self.region = region_name
        return self

    def Table(self, name):  # noqa: N802 - boto3 API name
        self.table = name
        return _FakeTable(name)


def _spy_boto3(monkeypatch):
    """Patch boto3.resource so get_dynamo_table builds against a fake; return the spy."""
    fake = _FakeResource()
    import boto3

    monkeypatch.setattr(boto3, "resource", fake)
    return fake


def test_cache_table_name_env_then_default(monkeypatch):
    monkeypatch.delenv("CACHE_TABLE", raising=False)
    monkeypatch.setenv("ENV_NAME", "prod")
    assert ac.cache_table_name() == "prod-wwii-api-cache"
    monkeypatch.setenv("CACHE_TABLE", "explicit-table")
    assert ac.cache_table_name() == "explicit-table"


def test_get_dynamo_table_uses_config_region(monkeypatch):
    spy = _spy_boto3(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "eu-west-3")
    monkeypatch.setenv("CACHE_TABLE", "t1")
    ac.get_dynamo_table()
    assert spy.region == "eu-west-3"  # from config.get_aws_region()
    assert spy.table == "t1"


def test_get_dynamo_table_explicit_overrides(monkeypatch):
    spy = _spy_boto3(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    ac.get_dynamo_table("other-table", region="ap-south-1")
    assert spy.region == "ap-south-1"
    assert spy.table == "other-table"


# --- routing: Flavor-A _table helpers go through get_dynamo_table ---------------------------
def test_credit_gate_table_routes_through_helper(monkeypatch):
    import src.utils.credit_gate as cg

    spy = mock.Mock(return_value="HANDLE")
    monkeypatch.setattr(ac, "get_dynamo_table", spy)
    assert cg._table() == "HANDLE"
    assert spy.called


def test_doc_lifecycle_table_routes_through_helper(monkeypatch):
    import src.ingestion.doc_lifecycle as dl

    spy = mock.Mock(return_value="HANDLE")
    monkeypatch.setattr(ac, "get_dynamo_table", spy)
    assert dl._table() == "HANDLE"
    assert spy.called


def test_nat_lease_table_routes_through_helper(monkeypatch):
    import src.utils.nat_lease as nl

    spy = mock.Mock(return_value="HANDLE")
    monkeypatch.setattr(ac, "get_dynamo_table", spy)
    assert nl._table(region="us-west-2") == "HANDLE"
    assert spy.called
    # region override threaded through
    _, kwargs = spy.call_args
    assert kwargs.get("region") == "us-west-2"
