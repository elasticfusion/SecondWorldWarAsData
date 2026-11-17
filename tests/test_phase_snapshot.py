"""scripts/phase_snapshot.py: back up the data set, let a "Phase 3 run" change it, restore it (moto).

Owner rules (2026-11-17): S3 output/ dedup/ manifests/ return exactly to the backup; cached Grok
answers are KEPT; every other known DynamoDB prefix returns to the snapshot (added rows deleted,
changed/deleted rows put back from the table backup); unknown keys are reported, never touched.
"""

import json

import boto3
import pytest
from moto import mock_aws

import scripts.phase_snapshot as ps

REGION = "us-east-1"
BUCKET = "dev-wwii-data-pipeline"
TABLE = "dev-wwii-api-cache"
H = "a" * 64  # a Grok request hash


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch, tmp_path):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    monkeypatch.setattr(ps, "pipeline_tasks", lambda *a: [])


def test_key_classes():
    assert ps.classify(f"people#{H}") == "grok"
    assert ps.classify(f"books/USA-E-XChannel/weather#{H}") == "grok"
    # same <word>#<64-hex> shape as a Grok answer, but the URL registry: reset
    assert ps.classify(f"url#{H}") == "reset"
    for k in (
        "search#grokipedia#970336607b7405ea",
        "lock#dev-wwii-phase3",
        "fetchevent#01M6",
        "exclusion#places#a.json#b.json",
        "manifest#phase2",
        "book_manifest#B#people",
    ):
        assert ps.classify(k) == "reset", k
    assert ps.classify("brand_new_prefix#x") == "unclassified"
    assert ps.classify("reaper#state") == "ignore"  # hourly heartbeat, never restored


def test_the_reaper_heartbeat_never_counts_as_a_difference():
    snap = {"reaper#state": {"cache_key": {"S": "reaper#state"}, "n": {"N": "0"}}}
    live = {"reaper#state": {"cache_key": {"S": "reaper#state"}, "n": {"N": "3"}}}
    assert ps.ddb_plan(live, snap) == {"delete": [], "put": [], "unclassified": []}


def _item(key, value):
    return {"cache_key": {"S": key}, "response": {"S": value}}


def _setup():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_versioning(
        Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    for key, body in {
        "output/people/a.json": "A",
        "output/maps/m.json": "M",
        "output/content/B/chapter1-parsed.json": "P",
        "dedup/review_status.json": "R",
        "manifests/reconcile_complete/_all.json": "X",
    }.items():
        s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode())
    ddb = boto3.client("dynamodb", region_name=REGION)
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    for k, v in {
        f"people#{H}": "old answer",
        "exclusion#places#a.json#b.json": "human",
        "search#grokipedia#1": "verdict",
        "manifest#phase2": "keys",
    }.items():
        ddb.put_item(TableName=TABLE, Item=_item(k, v))
    return s3, ddb, boto3.client("ecs", region_name=REGION)


def _phase3_run(s3, ddb):
    """What a Phase 3 run does to the data."""
    s3.put_object(Bucket=BUCKET, Key="output/people/a.json", Body=b"A enriched")
    s3.put_object(Bucket=BUCKET, Key="output/maps/new.json", Body=b"N")
    s3.delete_object(Bucket=BUCKET, Key="dedup/review_status.json")
    ddb.put_item(TableName=TABLE, Item=_item(f"people#{H}", "new answer"))  # Grok: kept
    ddb.put_item(
        TableName=TABLE, Item=_item(f"places#{'b' * 64}", "phase3 answer")
    )  # kept
    ddb.put_item(TableName=TABLE, Item=_item("search#openserp#2", "REJECT"))  # deleted
    ddb.put_item(
        TableName=TABLE, Item=_item("exclusion#places#a.json#b.json", "edited")
    )
    ddb.delete_item(TableName=TABLE, Key={"cache_key": {"S": "manifest#phase2"}})


def _live(ddb):
    return {k: v["response"]["S"] for k, v in ps.scan_items(ddb, TABLE).items()}


def _body(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


@mock_aws
def test_backup_then_restore_returns_to_the_snapshot_and_keeps_grok_answers():
    s3, ddb, ecs = _setup()
    manifest = ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    assert manifest["s3_objects"] == {"output": 3, "dedup": 1, "manifests": 1}
    assert manifest["dynamodb_keys"] == 4
    _phase3_run(s3, ddb)

    plan = ps.make_plan(s3, ddb, "dev", "p2-test", log=lambda m: None)
    assert plan["s3"]["output"] == {
        "copy": ["people/a.json"],
        "delete": ["maps/new.json"],
    }
    assert plan["s3"]["dedup"]["copy"] == ["review_status.json"]
    assert plan["ddb"]["delete"] == ["search#openserp#2"]
    assert plan["ddb"]["put"] == ["exclusion#places#a.json#b.json", "manifest#phase2"]
    assert ps.report(plan, log=lambda m: None) == (6, 0)

    assert (
        ps._restore(
            s3,
            ddb,
            "dev",
            "p2-test",
            plan,
            {"table_restore": True, "allow_phase2_trigger": False},
        )
        == 0
    )
    assert _body(s3, "output/people/a.json") == b"A"
    assert "maps/new.json" not in ps.list_objects(s3, BUCKET, "output")
    assert _body(s3, "dedup/review_status.json") == b"R"
    live = _live(ddb)
    assert live["exclusion#places#a.json#b.json"] == "human"
    assert live["manifest#phase2"] == "keys"
    assert "search#openserp#2" not in live
    # Grok answers made or changed during Phase 3 are kept as they are
    assert (
        live[f"people#{H}"] == "new answer"
        and live[f"places#{'b' * 64}"] == "phase3 answer"
    )
    # the temporary snapshot table is gone
    assert ddb.list_tables()["TableNames"] == [TABLE]


@mock_aws
def test_unknown_keys_are_left_alone_and_fail_verify():
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    ddb.put_item(TableName=TABLE, Item=_item("brand_new_prefix#x", "?"))
    plan = ps.make_plan(s3, ddb, "dev", "p2-test", log=lambda m: None)
    assert plan["ddb"]["unclassified"] == ["brand_new_prefix#x"]
    assert ps.report(plan, log=lambda m: None) == (0, 1)
    ps.apply(s3, ddb, "dev", "p2-test", plan, log=lambda m: None)
    assert "brand_new_prefix#x" in _live(ddb)


@mock_aws
def test_copying_back_a_parsed_file_is_refused_unless_allowed():
    """A parsed chapter file landing in output/content/ starts Phase 2 (S3 -> SNS -> trigger)."""
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    s3.put_object(
        Bucket=BUCKET, Key="output/content/B/chapter1-parsed.json", Body=b"changed"
    )
    plan = ps.make_plan(
        s3, ddb, "dev", "p2-test", table_restore=False, log=lambda m: None
    )
    assert ps.triggering_copies(plan) == ["output/content/B/chapter1-parsed.json"]
    opts = {"table_restore": False, "allow_phase2_trigger": False}
    with pytest.raises(ps.ResetError, match="starts Phase 2"):
        ps._restore(s3, ddb, "dev", "p2-test", plan, opts)
    assert _body(s3, "output/content/B/chapter1-parsed.json") == b"changed"
    assert (
        ps._restore(
            s3, ddb, "dev", "p2-test", plan, {**opts, "allow_phase2_trigger": True}
        )
        == 0
    )
    assert _body(s3, "output/content/B/chapter1-parsed.json") == b"P"


@mock_aws
def test_key_list_mode_only_deletes_added_rows():
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    _phase3_run(s3, ddb)
    plan = ps.make_plan(
        s3, ddb, "dev", "p2-test", table_restore=False, log=lambda m: None
    )
    assert plan["ddb"] == {
        "delete": ["search#openserp#2"],
        "put": [],
        "unclassified": [],
    }
    assert ddb.list_tables()["TableNames"] == [TABLE]  # no temporary table


@mock_aws
def test_a_used_name_or_a_missing_backup_is_refused():
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    with pytest.raises(ps.ResetError, match="already exists"):
        ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    with pytest.raises(ps.ResetError, match="no complete backup"):
        ps.make_plan(s3, ddb, "dev", "nope", log=lambda m: None)
    keys = json.loads(_body(s3, "backups/p2-test/dynamodb_keys.json"))
    assert sorted(keys) == sorted(_live(ddb))


def test_running_pipeline_tasks_block_every_mode(monkeypatch):
    monkeypatch.setattr(ps, "pipeline_tasks", lambda *a: ["arn:task/1"])
    with pytest.raises(ps.ResetError, match="pipeline task"):
        ps._refuse_if_busy(object(), "dev")


def test_only_the_openserp_search_server_is_exempt_from_the_busy_check():
    """The OpenSERP WORKER writes output/ and DynamoDB, so it blocks a backup or restore; the
    search server writes nothing."""

    class ECS:
        def get_paginator(self, _n):
            class P:
                def paginate(self, **k):
                    running = k["desiredStatus"] == "RUNNING"
                    return [{"taskArns": ["t1", "t2", "t3"]}] if running else [{}]

            return P()

        def describe_tasks(self, cluster, tasks):
            return {
                "tasks": [
                    {"taskArn": "t1", "group": "service:dev-wwii-openserp"},
                    {"taskArn": "t2", "group": "service:dev-wwii-openserp-worker"},
                    {"taskArn": "t3", "group": "family:dev-wwii-pipeline"},
                ]
            }

    import importlib

    real = importlib.reload(ps)  # the autouse fixture stubbed pipeline_tasks
    exempt = frozenset({"service:dev-wwii-openserp"})
    assert real.pipeline_tasks(ECS(), "c", exempt) == ["t2", "t3"]


@mock_aws
def test_restore_is_not_clean_while_unclassified_keys_remain():
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    ddb.put_item(TableName=TABLE, Item=_item("brand_new_prefix#x", "?"))
    plan = ps.make_plan(s3, ddb, "dev", "p2-test", log=lambda m: None)
    opts = {"table_restore": True, "allow_phase2_trigger": False}
    assert ps._restore(s3, ddb, "dev", "p2-test", plan, opts) == 1


@mock_aws
def test_phase3_fetch_claims_are_reset():
    assert ps.classify("contenthash#" + "c" * 64) == "reset"


@mock_aws
def test_an_expired_snapshot_row_is_not_put_back():
    s3, ddb, ecs = _setup()
    expired = {**_item("search#old#1", "x"), "ttl": {"N": "1"}}
    ddb.put_item(TableName=TABLE, Item=expired)
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    ddb.delete_item(TableName=TABLE, Key={"cache_key": {"S": "search#old#1"}})
    plan = ps.make_plan(s3, ddb, "dev", "p2-test", log=lambda m: None)
    assert "search#old#1" not in plan["ddb"]["put"]


@mock_aws
def test_a_leftover_temporary_table_is_refused_with_the_cleanup_command():
    s3, ddb, ecs = _setup()
    ps.backup(s3, ddb, ecs, "dev", "p2-test", log=lambda m: None)
    ddb.create_table(
        TableName=f"{TABLE}-snap-p2-test",
        KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    with pytest.raises(ps.ResetError, match="delete-table --table-name"):
        ps.make_plan(s3, ddb, "dev", "p2-test", log=lambda m: None)


def test_cli_refuses_a_bad_name_and_a_non_dev_env():
    with pytest.raises(SystemExit):
        ps.main(["verify", "--name", "x"])
    with pytest.raises(SystemExit, match="refused"):
        ps.main(["verify", "--name", "p2-test", "--env", "prod"])
