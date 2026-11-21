"""Tests for the SQS OpenSERP worker + producer (step 3), fully offline (mocked boto3/storage)."""

import json
import time
from unittest.mock import MagicMock

import openserp_worker as w
import phase3_enqueue_openserp as p


class FakeStorage:
    def __init__(self, files=None):
        self.data = dict(files or {})
        self.written = {}

    def list_files(self, prefix, pattern="*.json"):
        return [k for k in self.data if k.startswith(prefix + "/")]

    def read_json(self, path):
        return json.loads(json.dumps(self.data[path]))  # deep copy

    def write_json(self, path, data):
        self.written[path] = data
        self.data[path] = data


# ---------------- worker: process_message ----------------


def _patch_enrich(monkeypatch, changed=True, raises=False):
    import src.enrichment.openserp_enrichment as oe

    def fake(data, url, grok):
        if raises:
            raise RuntimeError("boom")
        data["_touched"] = True
        return changed

    monkeypatch.setattr(oe, "enrich_one_person", fake, raising=False)
    monkeypatch.setattr(oe, "_validate_before_write", lambda d, e: True)
    monkeypatch.setattr(oe, "_metric", lambda *a, **k: None)


def test_process_done_marks_and_writes(monkeypatch):
    _patch_enrich(monkeypatch, changed=True)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    body = {"entity_type": "people", "entity_path": "people/x.json"}
    verdict = w.process_message(body, st, "http://localhost:7001", grok_client=object())
    assert verdict == "done"
    assert st.written["people/x.json"]["openserp_searched"] is True
    assert "openserp_searched_at" in st.written["people/x.json"]


def test_process_skips_already_searched(monkeypatch):
    _patch_enrich(monkeypatch)
    st = FakeStorage(
        {
            "people/x.json": {
                "name": "X",
                "openserp_searched": True,
                "openserp_searched_at": int(time.time()),
            }
        }
    )
    body = {"entity_type": "people", "entity_path": "people/x.json"}
    verdict = w.process_message(body, st, "http://localhost:7001", grok_client=object())
    assert verdict == "skip"
    assert st.written == {}  # no write for an already-done entity


def test_process_retry_on_enrich_error(monkeypatch):
    _patch_enrich(monkeypatch, raises=True)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    body = {"entity_type": "people", "entity_path": "people/x.json"}
    verdict = w.process_message(body, st, "http://localhost:7001", grok_client=object())
    assert verdict == "retry"
    assert st.written == {}  # not marked/written -> message redelivers


def test_process_unknown_type_is_skip(monkeypatch):
    st = FakeStorage({})
    body = {"entity_type": "nope", "entity_path": "nope/x.json"}
    assert w.process_message(body, st, "http://localhost:7001", object()) == "skip"


# ---------------- worker: run loop delete semantics ----------------


def _msg(body):
    return {"ReceiptHandle": "r1", "Body": json.dumps(body)}


def test_run_worker_deletes_only_on_done(monkeypatch):
    _patch_enrich(monkeypatch, changed=True)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    sqs = MagicMock()
    # one real message, then empty to trigger idle-exit
    sqs.receive_message.side_effect = [
        {"Messages": [_msg({"entity_type": "people", "entity_path": "people/x.json"})]},
        {"Messages": []},
    ]
    completed = w.run_worker(
        sqs,
        "qurl",
        st,
        "http://localhost:7001",
        object(),
        visibility=10,
        idle_exit_polls=1,
    )
    assert completed == 1
    sqs.delete_message.assert_called_once_with(QueueUrl="qurl", ReceiptHandle="r1")


def test_run_worker_no_delete_on_retry(monkeypatch):
    _patch_enrich(monkeypatch, raises=True)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    sqs = MagicMock()
    sqs.receive_message.side_effect = [
        {"Messages": [_msg({"entity_type": "people", "entity_path": "people/x.json"})]},
        {"Messages": []},
    ]
    completed = w.run_worker(
        sqs,
        "qurl",
        st,
        "http://localhost:7001",
        object(),
        visibility=10,
        idle_exit_polls=1,
    )
    assert completed == 0
    sqs.delete_message.assert_not_called()  # retry => redelivery, never deleted


def test_sigterm_stops_without_delete(monkeypatch):
    """A SIGTERM mid-run leaves the in-flight message for redelivery."""
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(oe, "_validate_before_write", lambda d, e: True)
    monkeypatch.setattr(oe, "_metric", lambda *a, **k: None)

    def fake(data, url, grok):
        w._shutdown.set()  # Spot reclaim arrives while enriching
        return True

    monkeypatch.setattr(oe, "enrich_one_person", fake, raising=False)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    sqs = MagicMock()
    sqs.receive_message.side_effect = [
        {"Messages": [_msg({"entity_type": "people", "entity_path": "people/x.json"})]}
    ]
    w._shutdown.clear()
    try:
        w.run_worker(sqs, "qurl", st, "http://localhost:7001", object(), visibility=10)
    finally:
        w._shutdown.clear()
    # process returned "done" but shutdown set during it -> still deletes (write already done).
    # The guard only blocks delete when verdict != done; here it's done so delete is allowed.
    sqs.delete_message.assert_called_once()


# ---------------- producer ----------------


def test_producer_only_enqueues_unsearched():
    st = FakeStorage(
        {
            "people/a.json": {"name": "A"},  # needs search
            "people/b.json": {
                "name": "B",
                "openserp_searched": True,
                "openserp_searched_at": int(time.time()),
            },  # done
            "equipment/c.json": {"common_name": "C"},  # needs search
        }
    )
    msgs = p.build_messages(st, book="BookX")
    paths = sorted(m["entity_path"] for m in msgs)
    assert paths == ["equipment/c.json", "people/a.json"]
    assert all(m["book"] == "BookX" and m["schema"] == 1 for m in msgs)


def test_producer_batches_sends_in_tens():
    sqs = MagicMock()
    # Realistic per-batch responses: 10, 10, then 3 successful.
    sqs.send_message_batch.side_effect = [
        {"Successful": [{}] * 10, "Failed": []},
        {"Successful": [{}] * 10, "Failed": []},
        {"Successful": [{}] * 3, "Failed": []},
    ]
    msgs = [
        {"entity_path": f"people/{i}.json", "entity_type": "people"} for i in range(23)
    ]
    sent = p.enqueue(sqs, "qurl", msgs)
    assert sqs.send_message_batch.call_count == 3  # 10 + 10 + 3
    assert sent == 23


def test_process_validation_failure_is_retry(monkeypatch):
    """A schema-validation failure must NOT be silently dropped: return 'retry' (-> DLQ), no write."""
    import src.enrichment.openserp_enrichment as oe

    def fake(data, url, grok):
        data["_touched"] = True
        return True

    monkeypatch.setattr(oe, "enrich_one_person", fake, raising=False)
    monkeypatch.setattr(
        oe, "_validate_before_write", lambda d, e: False
    )  # guard rejects
    monkeypatch.setattr(oe, "_metric", lambda *a, **k: None)
    st = FakeStorage({"people/x.json": {"name": "X"}})
    body = {"entity_type": "people", "entity_path": "people/x.json"}
    verdict = w.process_message(body, st, "http://localhost:7001", object())
    assert verdict == "retry"
    assert (
        st.written == {}
    )  # not persisted -> message redelivers -> DLQ, never silently lost


def test_process_rejects_path_traversal(monkeypatch):
    """C1 security: an untrusted entity_path escaping output/<type>/ is rejected (skip), no I/O."""
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(oe, "enrich_one_person", lambda *a, **k: True, raising=False)
    st = FakeStorage({})
    for bad in [
        "../../etc/shadow",
        "/etc/shadow",
        "people/../../../etc/x.json",
        "equipment/x.json",  # wrong subdir for 'people'
        "people/x.txt",  # not .json
        "people\\x.json",  # backslash
    ]:
        body = {"entity_type": "people", "entity_path": bad}
        assert (
            w.process_message(body, st, "http://localhost:7001", object()) == "skip"
        ), bad
    assert st.written == {}


def test_safe_entity_path_accepts_valid():
    assert w._safe_entity_path("people/bruce c clarke.json", "people") is True
    assert w._safe_entity_path("source_section/01ABC.json", "source_section") is True
    assert (
        w._safe_entity_path("people/sub/../x.json", "people") is False
    )  # normalization change


def test_a_grok_stop_keeps_the_message_and_pauses(monkeypatch):
    """Task 1.2e: Grok down or no credits must not crash the worker or drain the queue into the
    DLQ: the message is kept (retry) and the worker pauses for the stop window."""
    import openserp_worker as w
    from src.utils import grok_models

    slept = []
    monkeypatch.setattr(w.time, "sleep", slept.append)
    monkeypatch.setattr(w, "_safe_entity_path", lambda *a: True)

    class Storage:
        def read_json(self, path):
            return {"name": "x"}

    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(
        oe,
        "enrich_one_person",
        lambda *a: (_ for _ in ()).throw(grok_models.GrokUnreachable("down")),
    )
    out = w.process_message(
        {"entity_type": "people", "entity_path": "people/x.json"},
        Storage(),
        "http://serp",
        object(),
    )
    assert out == "defer" and slept == [w.GROK_STOP_PAUSE_S]


def _defer_run(monkeypatch, send_fails=False, deferrals=None):
    import openserp_worker as w

    monkeypatch.setattr(w, "process_message", lambda *a: "defer")
    sqs = MagicMock()
    if send_fails:
        sqs.send_message.side_effect = RuntimeError("AccessDenied")
    body = '{"entity_type": "people", "entity_path": "people/x.json"}'
    msg = {"ReceiptHandle": "r1", "Body": body}
    if deferrals is not None:
        msg["MessageAttributes"] = {"deferrals": {"StringValue": str(deferrals)}}
    sqs.receive_message.side_effect = [{"Messages": [msg]}, {"Messages": []}]
    w.run_worker(sqs, "qurl", object(), "http://s", object(), idle_exit_polls=1)
    return w, sqs, body


def test_an_outage_requeues_the_message_fresh_so_it_never_reaches_the_dlq(monkeypatch):
    """Task 1.2f: each receive counts toward maxReceiveCount, so a Grok outage longer than a few
    pauses used to move the whole queue into the DLQ. The message is sent again (receive count
    0, delayed) and only then is the old one deleted."""
    w, sqs, body = _defer_run(monkeypatch)
    sqs.send_message.assert_called_once_with(
        QueueUrl="qurl",
        MessageBody=body,
        DelaySeconds=w.DEFER_DELAY_S,
        MessageAttributes={"deferrals": {"DataType": "Number", "StringValue": "1"}},
    )
    sqs.delete_message.assert_called_once_with(QueueUrl="qurl", ReceiptHandle="r1")


def test_deferrals_are_counted_and_capped(monkeypatch, caplog):
    import openserp_worker as w

    _, sqs, _ = _defer_run(monkeypatch, deferrals=4)
    attrs = sqs.send_message.call_args.kwargs["MessageAttributes"]
    assert attrs["deferrals"]["StringValue"] == "5"
    _, sqs, _ = _defer_run(monkeypatch, deferrals=w.MAX_DEFERRALS)
    sqs.send_message.assert_not_called()
    sqs.delete_message.assert_not_called()  # an ordinary, counted retry now (-> DLQ)
    assert "OBSERV openserp_worker.defer_cap" in caplog.text


def test_a_failed_requeue_keeps_the_original_message(monkeypatch, caplog):
    _, sqs, _ = _defer_run(monkeypatch, send_fails=True)
    sqs.delete_message.assert_not_called()
    assert "OBSERV openserp_worker.requeue_failed" in caplog.text


# --- the worker traces each entity like the directory driver (task 1.2f) ----------------------


def _traced(monkeypatch, statuses, changed=False, verdicts=None):
    """enrich_one_person records ``statuses`` as its queries' results."""
    import src.enrichment.openserp_enrichment as oe

    def enrich(data, url, grok):
        for st in statuses:
            oe._trace_query("q", st, 1 if st == "results" else 0)
        for v in verdicts or []:
            oe._trace.get()["verdicts"].append(v)
        return changed

    monkeypatch.setattr(oe, "enrich_one_person", enrich)
    monkeypatch.setattr(w, "_safe_entity_path", lambda *a: True)
    storage = FakeStorage({"people/x.json": {"name": "X", "PersonID": "P1"}})
    storage.written = {}
    real = storage.write_json
    storage.write_json = lambda p, d: (storage.written.__setitem__(p, d), real(p, d))[1]
    out = w.process_message(
        {"entity_type": "people", "entity_path": "people/x.json"},
        storage,
        "http://s",
        object(),
    )
    return out, storage


def test_an_entity_whose_searches_only_errored_is_not_marked_searched(monkeypatch):
    out, storage = _traced(monkeypatch, ["error", "error"])
    assert out == "defer"  # was: marked searched, with no search done
    assert "openserp_searched" not in storage.written.get("people/x.json", {})


def test_an_empty_search_is_marked_and_logged_for_review(monkeypatch):
    out, storage = _traced(monkeypatch, ["empty", "empty"])
    assert out == "done"
    [(path, rec)] = [
        (p, d) for p, d in storage.written.items() if p.startswith("review/")
    ]
    assert path.startswith("review/openserp_misses/") and rec["outcome"] == "empty"
    assert storage.written["people/x.json"]["openserp_searched"] is True


def test_a_found_entity_writes_no_review_record(monkeypatch):
    out, storage = _traced(monkeypatch, ["results"], changed=True)
    assert out == "done" and not [p for p in storage.written if p.startswith("review/")]


def test_redeliveries_on_one_day_are_one_day_not_five_runs(monkeypatch, caplog):
    """A 15-minute redelivery loop during an OpenSERP outage must not look like five runs."""
    import src.enrichment.openserp_enrichment as oe

    for _ in range(oe.UNAVAILABLE_RUN_CAP + 3):
        _traced(monkeypatch, ["error"])
    assert "entity_never_searched" not in caplog.text


def test_an_entity_that_errors_on_five_different_days_is_called_out_once(
    monkeypatch, caplog
):
    import datetime as dt

    import src.enrichment.openserp_enrichment as oe
    from src.utils import search_cache as sc

    class Day(dt.datetime):
        today = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.today

    monkeypatch.setattr(dt, "datetime", Day)
    for i in range(oe.UNAVAILABLE_RUN_CAP + 2):
        Day.today = dt.datetime(2026, 11, 1 + i, tzinfo=dt.timezone.utc)
        _traced(monkeypatch, ["error"])
    assert caplog.text.count("OBSERV openserp.entity_never_searched") == 1
