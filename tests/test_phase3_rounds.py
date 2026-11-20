"""Phase 3 batch ROUNDS (Group 2 step 2): repeat the whole step pass on the answers until a pass
queues nothing; a round not answered in time is handed to the poller; the cap is soft (a human can
approve more); a request that never gets an answer is set aside."""

import json
import types
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import phase3_enrich_data as P
from src.grok_client import BatchModeCollecting
from src.utils import batch_rounds, round_approval
from src.utils.batch_api import BatchCollector, BatchPending, BatchRequest


class FakeGrok:
    """submit_batch answers the queue (or hands it off) and empties the collector."""

    def __init__(self, handoff_at=None):
        self.batch_mode = True
        self._batch_collector = BatchCollector()
        self.submitted = []
        self.handoff_at = handoff_at

    def submit_batch(self, batch_name, max_wait_minutes=None):
        self.submitted.append(
            (batch_name, [r.request_id for r in self._batch_collector.requests])
        )
        self._batch_collector = None
        self.batch_mode = False
        if self.handoff_at and len(self.submitted) == self.handoff_at:
            raise BatchPending("batch-9", batch_name)
        return f"batch-{len(self.submitted)}"


def _q(grok, *keys):
    for k in keys:
        grok._batch_collector.add(
            BatchRequest(k, [{"role": "user", "content": k}], "m", 0.1, "places")
        )


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Run the rounds loop with a scripted step pass: ``script`` is a list of key-lists, one per pass
    after the first (what each pass queues)."""
    monkeypatch.delenv("BATCH_ROUND_START", raising=False)
    monkeypatch.setenv("BOOK_NAME", "BookA")
    alerts = []
    monkeypatch.setattr(
        batch_rounds, "publish_alert", lambda s, m: alerts.append((s, m))
    )
    monkeypatch.setattr(round_approval, "approved_extra", lambda phase, scope: 0)

    def go(script, config=None, handoff_at=None, first=("k1",)):
        grok = FakeGrok(handoff_at)
        _q(grok, *first)
        passes = iter(script)

        def steps(args, cfg, g, stats, workers):
            keys = next(passes, [])
            if g._batch_collector is not None:
                _q(g, *keys)
            stats["places"] = {"enriched": 1, "status": "pending" if keys else "ok"}
            return 1

        monkeypatch.setattr(P, "_run_enrichment_steps", steps)
        args = types.SimpleNamespace(output_dir=tmp_path)
        stats = {}
        out = P._run_batch_rounds(args, config or {}, grok, stats, 1, 10)
        return out, grok, stats, alerts

    return go


def test_the_pass_repeats_until_nothing_is_queued(run):
    (enriched, stop), grok, stats, alerts = run([["k2"], ["k3"], []])
    assert stop is None
    assert [keys for _, keys in grok.submitted] == [["k1"], ["k2"], ["k3"]]
    assert enriched == 10 + 3  # the first pass's 10 plus one per repeated pass
    assert grok.batch_mode is False and grok._batch_collector is None
    assert alerts == []
    assert [n.rsplit("-", 2)[-2:] for n, _ in grok.submitted] == [
        ["r1", "1reqs"],
        ["r2", "1reqs"],
        ["r3", "1reqs"],
    ]


def test_a_round_not_answered_in_time_is_handed_to_the_poller(run, tmp_path):
    (enriched, stop), grok, _, _ = run([["k2"]], handoff_at=1)
    assert stop == batch_rounds.HANDED_OFF_EXIT
    res = json.loads((tmp_path / ".phase_results.json").read_text())
    assert res["pending_batch"] == "batch-9" and res["pending_batch_name"].endswith(
        "-r1-1reqs"
    )
    assert len(grok.submitted) == 1  # nothing more is submitted


def test_a_resumed_run_continues_the_round_count(run, monkeypatch):
    monkeypatch.setenv("BATCH_ROUND_START", "4")
    _, grok, _, _ = run([[]])
    assert grok.submitted[0][0].endswith("-r4-1reqs")


def test_the_soft_cap_stops_the_run_and_alerts_with_the_approval_command(run, tmp_path):
    (_, stop), grok, _, alerts = run(
        [["k2"], ["k3"], ["k4"]], config={"batch": {"max_rounds": 2}}
    )
    assert stop == 1
    assert len(grok.submitted) == 2  # the third round was NOT sent
    res = json.loads((tmp_path / ".phase_results.json").read_text())
    assert "over cap" in res["incomplete"]
    subject, text = alerts[-1]
    assert "Phase 3 batch rounds over cap" in subject
    assert "approve_batch_rounds.py --phase phase3 --scope BookA" in text


def test_an_approval_lets_the_run_past_the_cap(run, monkeypatch):
    monkeypatch.setattr(
        round_approval,
        "approved_extra",
        lambda phase, scope: 2 if phase == "phase3" and scope == "BookA" else 0,
    )
    (_, stop), grok, _, alerts = run(
        [["k2"], ["k3"], []], config={"batch": {"max_rounds": 2}}
    )
    assert stop is None and len(grok.submitted) == 3 and alerts == []


def test_a_request_that_never_gets_an_answer_is_set_aside_once(run, caplog):
    # k1 is asked again after every round: submitted 3 times, then set aside and the run ends
    (_, stop), grok, stats, alerts = run(
        [["k1"], ["k1"], ["k1"], ["k1"]], config={"batch": {"max_request_attempts": 3}}
    )
    assert stop is None
    assert [keys for _, keys in grok.submitted] == [["k1"], ["k1"], ["k1"]]
    assert len(alerts) == 1 and "set aside" in alerts[0][0]
    assert "phase3.steps_pending" in caplog.text and "places" in caplog.text
    # a step left pending (requests set aside) means the book is NOT finished: it stays queued
    from src.utils import stop_cause

    assert stats["places"]["status"] == "pending"
    assert P._exit_for_steps(stats) == stop_cause.EXIT_STEP_FAILED


def test_other_requests_continue_when_one_is_set_aside(run):
    (_, stop), grok, _, alerts = run(
        [["k1", "k2"], ["k1"], ["k1"]], config={"batch": {"max_request_attempts": 2}}
    )
    assert stop is None
    assert [keys for _, keys in grok.submitted] == [["k1"], ["k1", "k2"]]
    assert len(alerts) == 1


def test_a_queued_step_is_pending_not_failed():
    stats = {}

    def queued():
        raise BatchModeCollecting("k")

    assert P.run_enrichment_step(stats, "places", queued) == 0
    assert stats["places"] == {"enriched": 0, "status": "pending"}
    assert P.run_enrichment_step(stats, "x", lambda: 3) == 3
    assert stats["x"] == {"enriched": 3, "status": "ok"}


def test_a_real_failure_is_still_an_error():
    stats = {}
    assert P.run_enrichment_step(stats, "places", lambda: 1 / 0) == 0
    assert stats["places"]["status"] == "error"
    assert P._exit_for_steps(stats) != 0


def test_discard_removes_requests_from_the_collector():
    c = BatchCollector()
    for k in ("a", "b", "c"):
        c.add(BatchRequest(k, [], "m", 0.1, "x"))
    assert c.discard({"a", "c", "zzz"}) == 2
    assert [r.request_id for r in c.requests] == ["b"]
    c.add(BatchRequest("a", [], "m", 0.1, "x"))  # can be queued again
    assert len(c) == 2


# --- approvals -----------------------------------------------------------------------------


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("CACHE_TABLE", "t-cache")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="t-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table("t-cache")


def test_approval_round_trip_scope_and_expiry(table):
    assert round_approval.approved_extra("phase3", "BookA") == 0
    assert round_approval.approve("phase3", "BookA", 4, who="dc") == 4
    assert round_approval.approved_extra("phase3", "BookA") == 4
    assert round_approval.approved_extra("phase3", "BookB") == 0  # another scope
    assert round_approval.approved_extra("phase2", "BookA") == 0  # another phase
    table.update_item(
        Key={"cache_key": round_approval.row_key("phase3", "BookA")},
        UpdateExpression="SET expires = :e",
        ExpressionAttributeValues={":e": 1},
    )
    assert round_approval.approved_extra("phase3", "BookA") == 0  # expired


def test_approval_is_bounded(table):
    assert round_approval.approve("phase3", "BookA", 10**6) == round_approval.MAX_EXTRA
    assert round_approval.approve("phase3", "BookA", -5) == 1


def test_no_table_means_no_approval_and_an_unreadable_one_is_loud(monkeypatch, caplog):
    monkeypatch.delenv("CACHE_TABLE", raising=False)
    assert round_approval.approved_extra("phase3", "x") == 0
    with pytest.raises(RuntimeError):
        round_approval.approve("phase3", "x", 1)
    monkeypatch.setenv("CACHE_TABLE", "missing-table")
    monkeypatch.setattr(
        round_approval, "_table", lambda: (_ for _ in ()).throw(OSError("x"))
    )
    assert round_approval.approved_extra("phase3", "x") == 0
    assert "batch.approval_unreadable" in caplog.text


def test_the_approval_script_writes_the_row(table, capsys):
    from scripts import approve_batch_rounds as S

    assert S.main(["--phase", "phase3", "--scope", "BookA", "--extra", "3"]) == 0
    assert "Approved 3 extra round(s)" in capsys.readouterr().out
    assert round_approval.approved_extra("phase3", "BookA") == 3


def test_a_run_with_every_step_answered_is_finished():
    assert P._exit_for_steps({"places": {"enriched": 4, "status": "ok"}}) == 0


def test_a_steps_count_adds_up_over_the_rounds_and_pending_keeps_it():
    stats = {}
    P.run_enrichment_step(stats, "places", lambda: 2)
    P.run_enrichment_step(stats, "places", lambda: 3)
    assert stats["places"] == {"enriched": 5, "status": "ok"}

    def queued():
        raise BatchModeCollecting("k")

    P.run_enrichment_step(stats, "places", queued)
    assert stats["places"] == {"enriched": 5, "status": "pending"}


def test_an_early_stop_keeps_the_step_stats_in_the_results_file(run, tmp_path):
    (_, stop), grok, stats, _ = run(
        [["k2"], ["k3"]], config={"batch": {"max_rounds": 1}}
    )
    res = json.loads((tmp_path / ".phase_results.json").read_text())
    assert stop == 1 and "source_stats" in res and "errored_sources" in res
    assert "pending_sources" in res


def test_the_cap_alert_quotes_the_scope_for_the_shell(monkeypatch):
    alerts = []
    monkeypatch.setattr(batch_rounds, "publish_alert", lambda s, m: alerts.append(m))
    monkeypatch.setattr(round_approval, "approved_extra", lambda p, s: 0)
    r = batch_rounds.BatchRounds(scope="a b;rm -rf x", cap=0, phase="phase3")
    assert not r.admit([types.SimpleNamespace(request_id="k", cache_type="t")])
    assert "--scope 'a b;rm -rf x'" in alerts[0]


def test_approval_scope_must_look_like_a_book_name(table):
    for bad in ("", "a b", "x;y", "../x", "a" * 200, "x\ny", "BookA\n"):
        with pytest.raises(ValueError):
            round_approval.approve("phase3", bad, 1)
    assert round_approval.approve("phase3", "USA-E-XChannel", 1) == 1
    assert round_approval.approve("phase3", "A,B", 1) == 1  # a multi-book scope


def test_a_malformed_approval_row_is_none_and_loud(table, caplog):
    table.put_item(
        Item={
            "cache_key": round_approval.row_key("phase3", "BookA"),
            "extra": "many",
            "expires": 9999999999,
        }
    )
    assert round_approval.approved_extra("phase3", "BookA") == 0
    assert "batch.approval_unreadable" in caplog.text


def test_no_service_role_may_write_an_approval_row():
    """Only an operator raises the cap: every role that can write the cache table denies rounds#*."""
    import yaml

    class L(yaml.SafeLoader):
        pass

    L.add_multi_constructor("!", lambda loader, suffix, node: None)
    root = Path(__file__).resolve().parent.parent / "cloudformation"
    for name in ("iam.yaml", "dispatcher.yaml", "ocr.yaml"):
        tpl = yaml.load((root / name).read_text(), Loader=L)
        denied = set()
        for res in tpl["Resources"].values():
            if res.get("Type") != "AWS::IAM::Role":
                continue
            for pol in res["Properties"].get("Policies", []):
                for st in pol["PolicyDocument"]["Statement"]:
                    if st.get("Effect") == "Deny":
                        keys = (
                            st.get("Condition", {})
                            .get("ForAnyValue:StringLike", {})
                            .get("dynamodb:LeadingKeys", [])
                        )
                        if "rounds#*" in keys:
                            denied.add(
                                res["Properties"]["RoleName"]
                                if isinstance(res["Properties"]["RoleName"], str)
                                else name
                            )
        assert denied or name == "dispatcher.yaml", name
    src = (root / "dispatcher.yaml").read_text()
    assert src.count("- rounds#*") == 2  # the dispatcher Lambda and its state machine
    assert (root / "ocr.yaml").read_text().count("- rounds#*") == 1
    assert (root / "iam.yaml").read_text().count(
        "- rounds#*"
    ) == 2  # task role + Lambda role
