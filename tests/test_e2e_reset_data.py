"""scripts/e2e_reset_data.py — the data half of the E2E reset (moto-backed).

Owner requirement: before an E2E rerun the reset deletes ALL derived S3 objects (every version of
a versioned bucket) and ALL queue-like DynamoDB data + SQS queues, while preserving the paid Grok
cache and source uploads. It must FAIL CLOSED (an error or unreadable resource is never 'clean').
"""

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import scripts.e2e_reset_data as er

REGION = "us-east-1"
BUCKET = "dev-wwii-data-pipeline"
CACHE = "dev-wwii-api-cache"


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch, tmp_path):
    """Hermetic: never see the developer's real/expired credentials or profile."""
    for k in (
        "AWS_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setattr(er.time, "sleep", lambda s: None)


def _ddb_table(ddb, name, hash_key, range_key=None):
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    attrs = [{"AttributeName": hash_key, "AttributeType": "S"}]
    if range_key:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
        attrs.append({"AttributeName": range_key, "AttributeType": "S"})
    ddb.create_table(
        TableName=name,
        KeySchema=schema,
        AttributeDefinitions=attrs,
        BillingMode="PAY_PER_REQUEST",
    )


def _put(ddb, table, **kv):
    ddb.put_item(TableName=table, Item={k: {"S": v} for k, v in kv.items()})


def _count(ddb, table):
    return ddb.scan(TableName=table, Select="COUNT")["Count"]


@pytest.fixture
def world():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION)
        sqs = boto3.client("sqs", region_name=REGION)
        _ddb_table(ddb, CACHE, "cache_key")
        for key in (
            "title#X",
            "pending#content",
            "manifest#phase2",
            "book_manifest#X#people",
            "lock#dev-wwii-phase3-enrich",
            "batch_job#b1",
            "dedup_run#people",
            "reaper#state",
            "nat#lease#gateway",
            "doc#BookA",
            "gate#phase3#x",
            "ocrctl#x",
            "reviewed#equipment#a#b",
            # must survive every mode:
            "fetchevent#1",
            "search#1",
            "url#1",
            "place_coords#1",
            "exclusion#equipment#a#b",
            "name_exclusion#equipment#x#y",
        ):
            _put(ddb, CACHE, cache_key=key)
        # entity tables: one with hash+range, one whose hash key is a RESERVED WORD ('name').
        _ddb_table(ddb, "dev-wwii-people", "PersonID", "sk")
        _ddb_table(ddb, "dev-wwii-places", "name")
        for i in range(60):  # > 25: exercises batching
            _put(ddb, "dev-wwii-people", PersonID=f"p{i}", sk="s")
        _put(ddb, "dev-wwii-places", name="Normandy")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(
            Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
        )
        for key in (
            "manifests/m.json",
            "output/people/a.json",
            "output/people/a.json",  # 2nd version of the same key
            "needs-review/x",
            "dedup/d",
            "person_photos/p.jpg",
            "contentrepository/KEEP/KEEP.html",
            "contentrepository/WIPE/WIPE.html",
            "output/content/WIPE/c-parsed.json",
        ):
            s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        s3.delete_object(Bucket=BUCKET, Key="dedup/d")  # leaves a delete marker
        urls = {
            q: sqs.create_queue(QueueName=f"dev-wwii-{q}")["QueueUrl"]
            for q in er.SQS_QUEUES
        }
        for u in urls.values():
            sqs.send_message(QueueUrl=u, MessageBody="m")
        yield ddb, s3, sqs, urls


def _cache_keys(ddb):
    return {i["cache_key"]["S"] for i in ddb.scan(TableName=CACHE)["Items"]}


PRESERVED = {
    "fetchevent#1",
    "search#1",
    "url#1",
    "place_coords#1",
    "exclusion#equipment#a#b",
    "name_exclusion#equipment#x#y",
}


def test_default_sweep_clears_runstate_incl_nat_blockers_and_manifests(world):
    ddb, s3, _sqs, _ = world
    assert er.sweep("dev", REGION, full=False, titles=[], log=lambda *_: None) == 0
    left = _cache_keys(ddb)
    assert PRESERVED <= left
    # lock#/batch_job#/nat# are what nat-manager's demand check reads: they must go in EVERY mode or
    # the NAT can never be deleted. The --full-only keys stay in default mode.
    gone = ("title", "pending", "manifest", "book_manifest", "lock", "batch_job", "nat")
    assert not {k for k in left if k.split("#")[0] in gone}
    assert {"reaper#state", "doc#BookA", "gate#phase3#x"} <= left
    assert _count(ddb, "dev-wwii-people") == 60  # entity tables untouched
    assert er.s3_left(s3, BUCKET, "manifests") == 0
    assert er.s3_left(s3, BUCKET, "output") > 0  # derived data untouched


def test_full_sweep_empties_everything_but_preserved(world):
    ddb, s3, sqs, urls = world
    assert er.sweep("dev", REGION, full=True, titles=["WIPE"], log=lambda *_: None) == 0
    # ALL run-state gone (incl. doc#/gate#/ocrctl#/reviewed#), cache + human rows kept
    assert _cache_keys(ddb) == PRESERVED
    assert _count(ddb, "dev-wwii-people") == 0  # 60 items, hash+range, batched
    assert _count(ddb, "dev-wwii-places") == 0  # reserved-word hash key ('name')
    for pfx in ("manifests", *er.FULL_S3_PREFIXES):
        # EVERY version and delete marker is gone, not just the visible objects.
        assert er.s3_left(s3, BUCKET, pfx) == 0, pfx
    assert er.s3_left(s3, BUCKET, "contentrepository/WIPE") == 0
    assert er.s3_left(s3, BUCKET, "output/content/WIPE") == 0
    assert er.s3_left(s3, BUCKET, "contentrepository/KEEP") == 1  # sources preserved
    for q in er.SQS_QUEUES:
        assert er.queue_depth(sqs, f"dev-wwii-{q}") == 0


def test_verify_clean_after_full_sweep_and_dirty_before(world):
    log = []
    assert er.verify("dev", REGION, True, ["WIPE"], log=log.append, wait_s=0) > 0
    er.sweep("dev", REGION, True, ["WIPE"], log=lambda *_: None)
    assert er.verify("dev", REGION, True, ["WIPE"], log=log.append, wait_s=0) == 0


def test_verify_detects_leftover_created_after_sweep(world):
    ddb, *_ = world
    er.sweep("dev", REGION, True, [], log=lambda *_: None)
    _put(ddb, "dev-wwii-places", name="late")
    log = []
    assert er.verify("dev", REGION, True, [], log=log.append, wait_s=0) == 1
    assert any("LEFT dev-wwii-places" in m for m in log)


def test_verify_is_fail_closed_on_unreadable_resource(world, monkeypatch):
    """An AWS error must count as NOT clean — the old shell turned errors into '0 leftovers'."""

    def _boom(*_a, **_k):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Scan"
        )

    er.sweep("dev", REGION, False, [], log=lambda *_: None)
    monkeypatch.setattr(er, "count_left", _boom)
    log = []
    assert er.verify("dev", REGION, False, [], log=log.append, wait_s=0) >= 1
    assert any("VERIFY-ERROR" in m for m in log)


def test_sweep_reports_failure_instead_of_claiming_success(world, monkeypatch):
    def _boom(*_a, **_k):
        raise ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Scan"
        )

    monkeypatch.setattr(er, "clear_table", _boom)
    log = []
    assert er.sweep("dev", REGION, False, [], log=log.append) >= 1
    assert any("FAILED" in m for m in log)


def test_missing_entity_table_is_skipped_not_an_error(world):
    ddb, *_ = world
    ddb.delete_table(TableName="dev-wwii-places")
    log = []
    assert er.sweep("dev", REGION, True, [], log=log.append) == 0
    assert any("table not found" in m for m in log)
    assert er.verify("dev", REGION, True, [], log=log.append, wait_s=0) == 0


def test_second_pass_can_skip_queues(world):
    _ddb, _s3, sqs, _ = world
    er.sweep("dev", REGION, True, [], log=lambda *_: None, queues=False)
    assert er.queue_depth(sqs, "dev-wwii-trigger-queue") == 1  # untouched


def test_batch_delete_retries_unprocessed_then_raises():
    class _Ddb:
        def __init__(self, fail_forever):
            self.calls, self.fail_forever = 0, fail_forever

        def batch_write_item(self, RequestItems):
            self.calls += 1
            ((table, reqs),) = RequestItems.items()
            if self.fail_forever or self.calls == 1:
                return {"UnprocessedItems": {table: reqs}}
            return {"UnprocessedItems": {}}

    ok = _Ddb(False)
    assert er._batch_delete(ok, "t", [{"k": {"S": str(i)}} for i in range(30)]) == 30
    assert ok.calls >= 3  # 25 retried once + the remaining 5
    with pytest.raises(er.ResetError, match="unprocessed"):
        er._batch_delete(_Ddb(True), "t", [{"k": {"S": "1"}}])


def test_purge_queue_retries_in_progress_then_gives_up_on_other_errors():
    def _err(code):
        return ClientError({"Error": {"Code": code, "Message": "m"}}, "PurgeQueue")

    class _Sqs:
        def __init__(self, errors):
            self.errors, self.purges = list(errors), 0

        def get_queue_url(self, QueueName):
            return {"QueueUrl": "http://q"}

        def purge_queue(self, QueueUrl):
            self.purges += 1
            if self.errors:
                raise self.errors.pop(0)

    s = _Sqs([_err("PurgeQueueInProgress")])
    assert er.purge_queue(s, "q") == "purged" and s.purges == 2
    with pytest.raises(ClientError):
        er.purge_queue(_Sqs([_err("AccessDenied")]), "q")


def test_validation_guards(monkeypatch):
    with pytest.raises(SystemExit, match="invalid --env"):
        er.main(["sweep", "--env", "Dev; rm -rf /"])
    with pytest.raises(SystemExit, match="invalid --wipe-title"):
        er.main(["sweep", "--wipe-title", "../x"])
    with pytest.raises(SystemExit, match="refused for env"):
        er.main(["sweep", "--env", "prod", "--full"])
    called = {}
    monkeypatch.setattr(er, "sweep", lambda *a, **k: called.setdefault("ok", 0))
    assert er.main(["sweep", "--env", "prod", "--full", "--allow-nondev-full"]) == 0
    assert called  # explicit override reaches the sweep


def test_s3_prefix_collision_and_batching(world, monkeypatch):
    """'output' must not match 'output-foo/…'; >batch versions are all removed."""
    _ddb, s3, _sqs, _ = world
    s3.put_object(Bucket=BUCKET, Key="output-foo/keep", Body=b"x")
    for i in range(7):
        s3.put_object(Bucket=BUCKET, Key="output/many/k", Body=bytes([i]))  # 7 versions
    monkeypatch.setattr(er, "_S3_BATCH", 3)
    assert er.empty_prefix(s3, BUCKET, "output") >= 7
    assert er.s3_left(s3, BUCKET, "output") == 0
    assert er.s3_left(s3, BUCKET, "output-foo") == 1


def test_all_cloudformation_sqs_queues_are_covered():
    """Every AWS::SQS::Queue defined in cloudformation/ is purged (Batch job queues are not SQS)."""
    import glob
    import re

    names = set()
    for f in glob.glob("cloudformation/*.yaml"):
        lines = open(f).read().splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "Type: AWS::SQS::Queue":
                for nxt in lines[i + 1 : i + 8]:
                    m = re.search(
                        r"QueueName: !Sub \$\{EnvironmentName\}-wwii-([a-z0-9-]+)", nxt
                    )
                    if m:
                        names.add(m.group(1))
    declared = sum(
        1
        for f in glob.glob("cloudformation/*.yaml")
        for line in open(f).read().splitlines()
        if line.strip() == "Type: AWS::SQS::Queue"  # exact: not ...::QueuePolicy
    )
    assert len(names) == declared, "a queue is named another way - extend this test"
    assert names <= set(er.SQS_QUEUES), names - set(er.SQS_QUEUES)
    assert er.SQS_QUEUES[0] == "trigger-queue"  # event-source-mapped queue purged first


def test_queues_are_purged_before_tables_are_cleared(world, monkeypatch):
    order = []
    real_purge, real_clear = er.purge_queue, er.clear_table
    monkeypatch.setattr(
        er, "purge_queue", lambda s, n: order.append(("q", n)) or real_purge(s, n)
    )
    monkeypatch.setattr(
        er,
        "clear_table",
        lambda d, t, p=(): order.append(("t", t)) or real_clear(d, t, p),
    )
    er.sweep("dev", REGION, True, [], log=lambda *_: None)
    kinds = [k for k, _ in order]
    assert kinds.index("t") > max(i for i, k in enumerate(kinds) if k == "q")


def test_boto_errors_other_than_clienterror_fail_closed(world, monkeypatch):
    from botocore.exceptions import NoCredentialsError

    def _boom(*_a, **_k):
        raise NoCredentialsError()

    monkeypatch.setattr(er, "clear_table", _boom)
    assert er.sweep("dev", REGION, False, [], log=lambda *_: None) >= 1
    monkeypatch.setattr(er, "count_left", _boom)
    assert er.verify("dev", REGION, False, [], log=lambda *_: None, wait_s=0) >= 1


def test_validate_mode_refuses_before_anything_runs():
    assert er.main(["validate", "--env", "dev", "--full"]) == 0
    with pytest.raises(SystemExit, match="refused for env"):
        er.main(["validate", "--env", "prod", "--full"])
    with pytest.raises(SystemExit, match="refused for env"):
        er.main(["validate", "--env", "prod"])  # default mode is destructive too
    assert er.main(["validate", "--env", "prod", "--allow-nondev-full"]) == 0


def test_verify_queue_loop_survives_boto_errors(world, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    def _boom(*_a, **_k):
        raise EndpointConnectionError(endpoint_url="http://x")

    monkeypatch.setattr(er, "queue_depth", _boom)
    log = []
    left = er.verify("dev", REGION, True, [], log=log.append, wait_s=0)
    assert left >= len(er.SQS_QUEUES)  # every queue checked, none aborted the loop
    assert any("leftovers:" in m for m in log)


SH = open(__file__.rsplit("/", 1)[0] + "/../scripts/e2e_reset.sh").read()


def test_shell_wrapper_ordering_and_safety():
    """Shell = orchestration only; refusal happens BEFORE any destructive step; NAT teardown runs
    AFTER the sweep; workers + autoscaler stopped; backstops restored on abort."""
    assert "dynamodb delete-item" not in SH and "s3 rm" not in SH
    first_destructive = SH.index('disable_backstops\n\necho "[2/6]')
    assert SH.index('"${DATA_PY[@]}" validate') < first_destructive
    assert SH.index('"${DATA_PY[@]}" sweep ||') < SH.index('--function-name "$NAT_FN"')
    assert "openserp-worker" in SH and "openserp-worker-autoscaler-schedule" in SH
    assert "trap _restore_backstops_on_exit EXIT" in SH
    assert "mktemp" in SH and "/tmp/e2e_reset_nat.json" not in SH
