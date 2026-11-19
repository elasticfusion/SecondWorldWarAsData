"""Task 1.1e (owner 2026-11-18): the trigger Lambda reads the xAI balance (through the
credit-check Lambda, no NAT) and does not launch a Grok phase under the floor - the task would
start only to stop at its preflight. Nothing is dropped: Phase 2/3 books stay queued, other
launches become credit holds replayed once credit is back. Unreadable here: launch (the task's
own preflight is the final word)."""

import io
import json
import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("PHASE1_TASK_DEF", "test-wwii-phase1-parse")
os.environ.setdefault("PHASE2_TASK_DEF", "test-wwii-phase2-extract")
os.environ.setdefault("PHASE3_TASK_DEF", "test-wwii-phase3-enrich")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from src.utils import credit_hold as cg  # noqa: E402

CONVERT = "test-wwii-phase0-convert"
VIDEO = "test-wwii-phase0-video"
ALLOWED = {CONVERT: {"CONVERT_KEY": cg.IMPORT_KEY}, VIDEO: {"VIDEO_KEY": cg.IMPORT_KEY}}
ENV = [{"name": "CONVERT_KEY", "value": "contentrepository/books/Utah.pdf"}]


@pytest.fixture(autouse=True)
def _no_remembered_failure(monkeypatch):
    monkeypatch.setattr(cg, "_failure", {})


@pytest.fixture
def table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        t = ddb.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield t


def _lambda(answer=None, error=False, raises=None):
    client = mock.Mock()
    if raises:
        client.invoke.side_effect = raises
    else:
        resp = {"Payload": io.BytesIO(json.dumps(answer or {}).encode())}
        if error:
            resp["FunctionError"] = "Unhandled"
        client.invoke.return_value = resp
    return client


def test_a_low_balance_is_not_allowed_and_a_good_one_is(table):
    assert cg.launch_allowed(table, _lambda({"usd": 12.5}), {}) == (False, 12.5, 50.0)
    table.delete_item(Key={"cache_key": cg.BALANCE_KEY})
    assert cg.launch_allowed(table, _lambda({"usd": 62.31}), {}, now=1e9) == (
        True,
        62.31,
        50.0,
    )


def test_the_floor_comes_from_config(table):
    cfg = {"api": {"grok": {"credit_floor_usd": 70, "credit_alert_usd": 5}}}
    assert cg.launch_allowed(table, _lambda({"usd": 62.31}), cfg)[0] is False


@pytest.mark.parametrize(
    "client",
    [
        _lambda({"error": "connectivity", "reason": "invoice preview: HTTP 503"}),
        _lambda({}, error=True),
        _lambda({"usd": "lots"}),
        _lambda(raises=RuntimeError("throttled")),
    ],
)
def test_an_unreadable_balance_lets_the_task_decide(table, client):
    allowed, usd, _ = cg.launch_allowed(table, client, {})
    assert allowed is True and usd is None


def test_a_failed_read_is_remembered_briefly(table):
    """An xAI outage must not make every launch in a burst wait on the credit-check Lambda."""
    client = _lambda({"error": "connectivity", "reason": "HTTP 503"})
    assert cg.balance(table, client, now=1000.0) == (None, "HTTP 503")
    assert cg.balance(table, client, now=1000.0 + cg.FAILURE_SECONDS - 1)[0] is None
    assert client.invoke.call_count == 1
    cg.balance(table, client, now=1000.0 + cg.FAILURE_SECONDS)
    assert client.invoke.call_count == 2


@pytest.mark.parametrize(
    "stored", [{"usd": 1.0, "at": 5000.0}, {"usd": "NaN", "at": 1000.0},
               {"usd": "Infinity", "at": 1000.0}],
)  # fmt: skip
def test_a_tampered_cache_is_not_trusted(table, stored):
    """A future time would look fresh forever; NaN/Infinity is not a balance."""
    table.put_item(Item={"cache_key": cg.BALANCE_KEY, "response": json.dumps(stored)})
    client = _lambda({"usd": 62.31})
    assert cg.balance(table, client, now=1000.0) == (62.31, "")
    client.invoke.assert_called_once()


def test_the_check_switched_off_never_reads(table):
    client = _lambda({"usd": 1.0})
    off = {"api": {"grok": {"balance_check": False}}}
    assert cg.launch_allowed(table, client, off)[0] is True
    client.invoke.assert_not_called()


def test_a_fresh_cached_balance_saves_the_invoke(table):
    table.put_item(
        Item={
            "cache_key": cg.BALANCE_KEY,
            "response": json.dumps({"usd": 80.0, "at": 1000.0}),
        }
    )
    client = _lambda({"usd": 1.0})
    assert cg.balance(table, client, now=1000.0 + cg.CACHE_SECONDS - 1) == (80.0, "")
    client.invoke.assert_not_called()
    assert cg.balance(table, client, now=1000.0 + cg.CACHE_SECONDS) == (1.0, "")


def test_the_held_alert_goes_out_once_per_window(table, monkeypatch):
    sent = []
    monkeypatch.setattr(cg, "observe_alert", lambda s, e, **k: sent.append((e, k)))
    assert cg.alert_held(table, 12.0, 50.0, "phase3 Utah", now=1000.0)
    assert not cg.alert_held(table, 11.0, 50.0, "phase2 Utah", now=2000.0)
    later = 1000.0 + cg.ALERT_HOURS * 3600 + 1
    assert cg.alert_held(table, 9.0, 50.0, "phase2 Utah", now=later)
    assert [e for e, _ in sent] == ["launches_held", "launches_held"]
    # a marker from the future (tampered, clock skew) does not silence the alert
    table.put_item(Item={"cache_key": cg.ALERT_KEY, "at": int(later + 10**6)})
    assert cg.alert_held(table, 9.0, 50.0, "phase2 Utah", now=later + 60)
    assert "$12.00" in sent[0][1]["note"] and "console.x.ai" in sent[0][1]["note"]


def test_a_hold_is_kept_until_it_launches_and_never_lost(table):
    a = cg.hold_request(table, CONVERT, "s3", "Utah", ENV)
    video = [{"name": "VIDEO_KEY", "value": "contentrepository/films/Film.mp4"}]
    b = cg.hold_request(table, VIDEO, "s3", "Film", video)
    held = cg.held_requests(table, ALLOWED)
    assert held[a]["extra_env"] == ENV and set(held) == {a, b}

    def launch(req):
        return req["book"] == "Utah"  # Film's lock is held: not launched

    assert cg.release_held(table, launch, held) == (1, 0)
    assert set(cg.held_requests(table, ALLOWED)) == {b}  # kept, not dropped
    assert cg.release_held(table, lambda r: True, cg.held_requests(table, ALLOWED)) == (
        1,
        0,
    )


def test_the_same_launch_held_twice_is_one_hold(table):
    """A redelivered upload event must not launch the import twice."""
    assert cg.hold_request(table, CONVERT, "s3", "Utah", ENV) == cg.hold_request(
        table, CONVERT, "s3-again", "Utah", ENV
    )
    assert len(cg.held_requests(table, ALLOWED)) == 1


def test_a_launch_that_raises_keeps_its_hold_and_the_others_still_go(table):
    rid = cg.hold_request(table, CONVERT, "s3", "Utah", ENV)
    video = [{"name": "VIDEO_KEY", "value": "contentrepository/films/Film.mp4"}]
    other = cg.hold_request(table, VIDEO, "s3", "Film", video)

    def launch(req):
        if req["book"] == "Utah":
            raise RuntimeError("ecs down")
        return True

    assert cg.release_held(table, launch, cg.held_requests(table, ALLOWED)) == (1, 1)
    assert set(cg.held_requests(table, ALLOWED)) == {rid}
    assert other != rid
    assert cg.release_held(table, lambda r: True, cg.held_requests(table, ALLOWED)) == (
        1,
        0,
    )


def test_holds_are_spread_over_shards(table):
    """No single item nears DynamoDB's 400 KB limit."""
    ids = [
        cg.hold_request(
            table,
            CONVERT,
            "s3",
            f"Book{n}",
            [{"name": "CONVERT_KEY", "value": f"contentrepository/b/B{n}.pdf"}],
        )
        for n in range(64)
    ]
    assert len({cg._shard_key(i) for i in ids}) > 4
    assert len(cg.held_requests(table, ALLOWED)) == 64
    assert set(cg.hold_keys()) >= {cg._shard_key(i) for i in ids}


def test_a_live_claim_blocks_a_second_reconcile_and_a_stale_one_is_retried(table):
    """Claimed, then the Lambda died before confirming: the hold is not lost - an hour later
    the stale claim is taken over."""
    rid = cg.hold_request(table, CONVERT, "s3", "Utah", ENV)
    held = cg.held_requests(table, ALLOWED)
    assert cg._claim(table, rid, now=1000.0)  # ... and the Lambda dies here
    launched = []
    assert cg.release_held(table, launched.append, held, now=1500.0) == (0, 0)
    assert launched == []
    later = 1000.0 + cg.CLAIM_STALE_SECONDS + 1
    assert cg.release_held(table, lambda r: True, held, now=later) == (1, 0)
    assert cg.held_requests(table, ALLOWED) == {}


@pytest.mark.parametrize(
    "req",
    [
        "{not json",
        json.dumps({"task_def": "test-wwii-ocr", "book": "Utah", "extra_env": []}),
        json.dumps({"task_def": CONVERT, "book": "../x", "extra_env": []}),
        json.dumps({"task_def": CONVERT, "book": "Utah",
                    "extra_env": [{"name": "AWS_ROLE", "value": "contentrepository/x"}]}),
        json.dumps({"task_def": CONVERT, "book": "Utah",
                    "extra_env": [{"name": "CONVERT_KEY", "value": "output/secret.json"}]}),
        json.dumps({"task_def": CONVERT, "book": "Utah",
                    "extra_env": [{"name": "CONVERT_KEY", "value": "contentrepository/../x"}]}),
        json.dumps({"task_def": CONVERT, "book": "Utah", "source": "s" * 65}),
        json.dumps({"task_def": CONVERT, "book": "unknown", "extra_env": []}),
        json.dumps({"task_def": CONVERT, "book": "..", "extra_env": []}),
        # a video key on the convert task: each task def has only its own env
        json.dumps({"task_def": CONVERT, "book": "Utah",
                    "extra_env": [{"name": "VIDEO_KEY", "value": "contentrepository/x"}]}),
    ],
)  # fmt: skip
def test_a_hold_the_gate_could_not_have_written_is_never_replayed(table, caplog, req):
    """The table is writable by other roles: a stored entry is not trusted to name a launch."""
    rid = cg.hold_request(table, CONVERT, "s3", "Utah", ENV)
    table.update_item(
        Key={"cache_key": cg._shard_key(rid)},
        UpdateExpression="SET #h.#id = :r",
        ExpressionAttributeNames={"#h": "holds", "#id": "forged"},
        ExpressionAttributeValues={":r": req},
    )
    held = cg.held_requests(table, ALLOWED)
    assert "forged" not in held and len(held) == 1
    assert "OBSERV credit.hold_invalid id=forged" in caplog.text


# --- the credit-check Lambda ----------------------------------------------------------------------


def test_the_credit_check_lambda_reads_stores_and_returns_the_balance(
    table, monkeypatch
):
    import lambda_handlers.credit_check as cc

    monkeypatch.setattr(cc, "_keys", {})
    sm = mock.Mock()
    sm.get_secret_value.side_effect = lambda SecretId: {
        "SecretString": "mgmt\n" if "management" in SecretId else "inference"
    }
    seen = {}

    def fake_balance(mgmt, api, session):
        seen.update(mgmt=mgmt, api=api)
        return 62.31

    monkeypatch.setattr(cc.grok_balance, "balance_usd", fake_balance)
    assert cc.handler(sm=sm, table=table) == {"usd": 62.31}
    assert seen == {"mgmt": "mgmt", "api": "inference"}
    assert (
        cg.balance(table, _lambda({"usd": 1.0}))[0] == 62.31
    )  # cached for the trigger


def test_the_credit_check_lambda_never_guesses(table, monkeypatch):
    import lambda_handlers.credit_check as cc

    monkeypatch.setattr(cc, "_keys", {})
    sm = mock.Mock()
    sm.get_secret_value.side_effect = RuntimeError("AccessDenied")
    assert cc.handler(sm=sm, table=table)["error"] == "config"

    sm = mock.Mock()
    sm.get_secret_value.return_value = {"SecretString": "k"}

    def unreadable(*a):
        raise cc.grok_balance.BalanceUnreadable("connectivity", "HTTP 503")

    monkeypatch.setattr(cc.grok_balance, "balance_usd", unreadable)
    assert cc.handler(sm=sm, table=table) == {
        "error": "connectivity",
        "reason": "HTTP 503",
    }
    assert table.get_item(Key={"cache_key": cg.BALANCE_KEY}).get("Item") is None


# --- the trigger --------------------------------------------------------------------------------


@pytest.fixture
def th(table, monkeypatch):
    from lambda_handlers import trigger_handler as th

    monkeypatch.setattr(th, "dynamo", table)
    monkeypatch.setattr(th, "_trigger_config", lambda: {})
    monkeypatch.setattr(cg, "alert_held", mock.Mock(return_value=True))
    return th


def _gate(monkeypatch, allowed, usd):
    monkeypatch.setattr(cg, "launch_allowed", lambda *a, **k: (allowed, usd, 50.0))


def test_a_phase3_launch_under_the_floor_stays_queued(th, monkeypatch):
    _gate(monkeypatch, False, 12.0)
    queued = []
    monkeypatch.setattr(
        th, "_queue_pending_enrich", lambda b, strict=False: queued.append((b, strict))
    )
    monkeypatch.setattr(th, "_launch_ecs_task", mock.Mock(side_effect=AssertionError))
    assert th._run_task(th.PHASE3_TASK_DEF, "s3", book_name="Utah") is False
    assert queued == [("Utah", True)]  # strict: a failed write must not count as kept
    cg.alert_held.assert_called_once()


def test_a_phase2_launch_under_the_floor_stays_queued(th, monkeypatch):
    _gate(monkeypatch, False, 12.0)
    queued = []
    monkeypatch.setattr(
        th, "_queue_pending_parsed_book", lambda b, strict=False: queued.append(strict)
    )
    assert th._run_task(th.PHASE2_TASK_DEF, "s3", book_name="Utah") is False
    assert queued == [True]


def test_an_import_under_the_floor_becomes_a_credit_hold(th, monkeypatch):
    _gate(monkeypatch, False, 12.0)
    monkeypatch.setenv("BOOK_METADATA", "1")
    env = [{"name": "CONVERT_KEY", "value": "contentrepository/books/Utah.pdf"}]
    assert th._run_task(th.PHASE0_TASK_DEF, "s3", "Utah", env) is False
    [req] = cg.held_requests(th.dynamo, th._hold_env_rules()).values()
    assert req["task_def"] == th.PHASE0_TASK_DEF and req["extra_env"] == env


def test_phase1_and_imports_without_grok_are_not_gated(th, monkeypatch):
    monkeypatch.setattr(cg, "launch_allowed", mock.Mock(side_effect=AssertionError))
    monkeypatch.setenv("BOOK_METADATA", "0")
    assert not th._held_for_credit(th.PHASE1_TASK_DEF, "s3", "Utah", None)
    assert not th._held_for_credit(th.PHASE0_TASK_DEF, "s3", "Utah", None)


def test_unreadable_or_enough_credit_launches(th, monkeypatch):
    for allowed, usd in ((True, None), (True, 62.31)):
        _gate(monkeypatch, allowed, usd)
        assert not th._held_for_credit(th.PHASE3_TASK_DEF, "s3", "Utah", None)


@pytest.mark.parametrize("phase", ["PHASE2_TASK_DEF", "PHASE3_TASK_DEF"])
def test_a_queue_row_that_cannot_be_written_launches_instead(th, monkeypatch, phase):
    """Never drop: the real queue helpers swallow a write error unless strict, so a failed
    write must reach the gate - which then launches and lets the task's preflight stop.
    """
    _gate(monkeypatch, False, 12.0)
    broken = mock.Mock()
    broken.update_item.side_effect = OSError("dynamo down")
    broken.put_item.side_effect = OSError("dynamo down")
    monkeypatch.setattr(th, "dynamo", broken)
    import src.utils.pending_queue as pq

    monkeypatch.setattr(pq, "queue_enrich", mock.Mock(side_effect=OSError("down")))
    assert not th._held_for_credit(getattr(th, phase), "s3", "Utah", None)


def test_an_import_hold_that_cannot_be_written_launches_instead(th, monkeypatch):
    _gate(monkeypatch, False, 12.0)
    monkeypatch.setenv("BOOK_METADATA", "1")
    monkeypatch.setattr(cg, "hold_request", mock.Mock(side_effect=OSError("down")))
    assert not th._held_for_credit(th.PHASE0_TASK_DEF, "s3", "Utah", ENV)


def test_the_hourly_check_releases_holds_once_credit_is_back(th, monkeypatch):
    cg.hold_request(th.dynamo, th.PHASE0_TASK_DEF, "s3", "Utah", ENV)
    launched = []
    monkeypatch.setattr(
        th, "_run_task", lambda *a, **k: launched.append((a, k)) or True
    )
    _gate(monkeypatch, False, 12.0)
    assert th._release_credit_holds() == 0 and launched == []
    _gate(monkeypatch, True, 62.31)
    assert th._release_credit_holds() == 1
    [(args, kwargs)] = launched
    assert args[:3] == (th.PHASE0_TASK_DEF, "s3", "Utah")
    assert kwargs == {"credit_checked": True}  # not gated twice
    assert cg.held_requests(th.dynamo, th._hold_env_rules()) == {}


def test_a_manual_trigger_that_is_held_says_queued(th, monkeypatch):
    monkeypatch.setattr(th, "_run_task", lambda *a, **k: False)
    out = th._handle_manual_trigger({"book": "Utah", "phase": "3"})
    assert out["status"] == "queued"


def test_an_unscoped_run_is_held_and_replayed(th, monkeypatch):
    """A manual or dedup-complete Phase 3 run with no book has no queue: it is a credit hold."""
    _gate(monkeypatch, False, 12.0)
    assert th._held_for_credit(th.PHASE3_TASK_DEF, "dedup-complete", "", None)
    [req] = cg.held_requests(th.dynamo, th._hold_env_rules()).values()
    assert req["task_def"] == th.PHASE3_TASK_DEF and req["book"] == ""


@pytest.mark.parametrize("allowed", [True, False])
def test_a_held_declared_count_replays_only_while_the_operator_allows_it(
    th, monkeypatch, allowed
):
    _gate(monkeypatch, False, 12.0)
    monkeypatch.setattr(th, "ALLOW_MANUAL_EXPECTED_COUNT", True)
    count = [{"name": "EXPECTED_COUNT", "value": "250"}]
    assert th._held_for_credit(th.PHASE2_TASK_DEF, "manual", "", count)
    monkeypatch.setattr(th, "ALLOW_MANUAL_EXPECTED_COUNT", allowed)
    held = cg.held_requests(th.dynamo, th._hold_env_rules())
    assert bool(held) is allowed


def test_any_gate_error_launches(th, monkeypatch):
    monkeypatch.setattr(th, "_credit_lambda", mock.Mock(side_effect=RuntimeError("x")))
    assert not th._held_for_credit(th.PHASE3_TASK_DEF, "s3", "Utah", None)


def test_the_scheduled_check_releases_holds_before_the_reconcile(th, monkeypatch):
    order = []
    monkeypatch.setattr(th, "_release_credit_holds", lambda: order.append("credit"))
    monkeypatch.setattr(
        th, "_reconcile_pending", lambda reason: order.append("reconcile")
    )
    monkeypatch.setattr(th, "TASK_FAMILIES", {})
    th._handle_scheduled_check()
    assert order == ["credit", "reconcile"]


def test_the_credit_check_role_reads_only_the_two_xai_secrets():
    from tests.test_spot_ladder import _compute

    res = _compute()
    stmts = res["CreditCheckRole"]["Properties"]["Policies"][0]["PolicyDocument"][
        "Statement"
    ]
    sm = next(s for s in stmts if s["Action"] == "secretsmanager:GetSecretValue")
    assert len(sm["Resource"]) == 2
    # the 6-character Secrets Manager suffix only: no sibling secret matches
    text = open(
        os.path.join(os.path.dirname(__file__), "..", "cloudformation", "compute.yaml"),
        encoding="utf-8",
    ).read()  # the loader drops !Sub strings: check the template text
    assert "xai-management-key-??????" in text and "grok-api-key-??????" in text
    assert "xai-management-key-*" not in text
    ddb = next(s for s in stmts if s["Action"] == "dynamodb:PutItem")
    assert ddb["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] == [
        "credit#balance"
    ]
    fn = res["CreditCheckFunction"]["Properties"]
    assert "VpcConfig" not in fn  # outside the VPC: no NAT needed
    trig = res["TriggerFunction"]["Properties"]["Environment"]["Variables"]
    assert "CREDIT_CHECK_FN" in trig


def test_phase_tasks_may_not_write_the_credit_gate_items():
    import yaml

    from tests.test_spot_ladder import _CfnLoader

    path = os.path.join(os.path.dirname(__file__), "..", "cloudformation", "iam.yaml")
    res = yaml.load(open(path, encoding="utf-8"), Loader=_CfnLoader)["Resources"]
    stmts = [
        st
        for p in res["ECSTaskRole"]["Properties"]["Policies"]
        for st in p["PolicyDocument"]["Statement"]
    ]
    deny = next(st for st in stmts if st.get("Sid") == "NoCreditGateWrites")
    assert deny["Effect"] == "Deny"
    keys = deny["Condition"]["ForAnyValue:StringLike"]["dynamodb:LeadingKeys"]
    assert set(keys) == {cg.HOLDS_PREFIX + "*", cg.BALANCE_KEY, cg.ALERT_KEY}


@pytest.mark.parametrize(
    "template,role,keys",
    [("ocr.yaml", "OcrSpotControllerRole", 3), ("dispatcher.yaml", "DispatcherStateMachineRole", 3),
     ("dispatcher.yaml", "DispatcherLambdaRole", 2)],
)  # fmt: skip
def test_other_roles_may_not_write_the_credit_gate_items(template, role, keys):
    import yaml

    from tests.test_spot_ladder import _CfnLoader

    path = os.path.join(os.path.dirname(__file__), "..", "cloudformation", template)
    res = yaml.load(open(path, encoding="utf-8"), Loader=_CfnLoader)["Resources"]
    stmts = [
        st
        for p in res[role]["Properties"]["Policies"]
        for st in p["PolicyDocument"]["Statement"]
    ]
    deny = next(st for st in stmts if st.get("Sid") == "NoCreditGateWrites")
    assert deny["Effect"] == "Deny"
    found = deny["Condition"]["ForAnyValue:StringLike"]["dynamodb:LeadingKeys"]
    assert len(found) == keys and cg.HOLDS_PREFIX + "*" in found


def test_a_snapshot_restore_keeps_held_launches_and_the_balance():
    from scripts import phase_snapshot as ps

    for key in cg.hold_keys() + [cg.BALANCE_KEY, cg.ALERT_KEY]:
        assert ps.classify(key) == "ignore"
    assert (
        ps.classify("credit#spend") == "reset"
    )  # the batch spend counter is run state


# --- the dispatcher and the batch submit ----------------------------------------------------------


def test_the_dispatcher_leaves_grok_docs_ready_while_credit_is_low(table, monkeypatch):
    import lambda_handlers.dispatcher_handlers as dh

    monkeypatch.setattr(cg, "launch_allowed", lambda *a, **k: (False, 12.0, 50.0))
    monkeypatch.setattr(cg, "alert_held", mock.Mock())
    monkeypatch.setattr(cg, "lambda_client", lambda: None)
    docs = [{"cache_key": "doc#1", "next_phase": "phase1"},
            {"cache_key": "doc#2", "next_phase": "phase2"},
            {"cache_key": "doc#3", "next_phase": "phase3"}]  # fmt: skip
    assert dh._hold_grok_work_if_credit_low(table, docs) == [docs[0]]
    cg.alert_held.assert_called_once()
    monkeypatch.setattr(cg, "launch_allowed", lambda *a, **k: (True, 62.0, 50.0))
    assert dh._hold_grok_work_if_credit_low(table, docs) == docs
    monkeypatch.setattr(cg, "launch_allowed", mock.Mock(side_effect=RuntimeError))
    assert dh._hold_grok_work_if_credit_low(table, docs) == docs  # gate error: dispatch


def test_a_batch_submit_under_the_floor_starts_no_task(monkeypatch):
    import lambda_handlers.batch_poller as bp

    monkeypatch.setattr(cg, "launch_allowed", lambda *a, **k: (False, 12.0, 50.0))
    monkeypatch.setattr(cg, "alert_held", mock.Mock())
    monkeypatch.setattr(cg, "lambda_client", lambda: None)
    monkeypatch.setattr(bp.boto3, "client", mock.Mock(side_effect=AssertionError))
    import src.utils.aws_clients as ac

    monkeypatch.setattr(ac, "get_dynamo_table", lambda **k: mock.Mock())
    out = bp._handle_submit({"action": "submit", "phase": "phase3", "book": "Utah"})
    assert out["status"] == "held" and "console.x.ai" in out["message"]
    monkeypatch.setattr(cg, "launch_allowed", lambda *a, **k: (True, None, 50.0))
    assert bp._credit_hold_reply("phase3", "Utah") == {}


def test_the_credit_check_lambda_rereads_a_rejected_key(table, monkeypatch):
    import lambda_handlers.credit_check as cc

    monkeypatch.setattr(cc, "_keys", {"x": "old"})
    sm = mock.Mock()
    sm.get_secret_value.return_value = {"SecretString": "k"}

    def rejected(*a):
        raise cc.grok_balance.BalanceUnreadable("config", "HTTP 401 (key rejected)")

    monkeypatch.setattr(cc.grok_balance, "balance_usd", rejected)
    cc.handler(sm=sm, table=table)
    assert cc._keys == {}


def test_a_large_backlog_is_released_a_slice_per_hour(table, monkeypatch):
    monkeypatch.setattr(cg, "RELEASE_PER_RUN", 3)
    for n in range(5):
        cg.hold_request(
            table,
            CONVERT,
            "s3",
            f"Book{n}",
            [{"name": "CONVERT_KEY", "value": f"contentrepository/b/B{n}.pdf"}],
        )
    assert cg.release_held(table, lambda r: True, cg.held_requests(table, ALLOWED)) == (
        3,
        0,
    )
    assert len(cg.held_requests(table, ALLOWED)) == 2


def test_the_enumerate_lambda_outlasts_a_slow_credit_check():
    import yaml

    from tests.test_spot_ladder import _CfnLoader

    path = os.path.join(
        os.path.dirname(__file__), "..", "cloudformation", "dispatcher.yaml"
    )
    res = yaml.load(open(path, encoding="utf-8"), Loader=_CfnLoader)["Resources"]
    assert res["EnumeratePendingFunction"]["Properties"]["Timeout"] >= 120
