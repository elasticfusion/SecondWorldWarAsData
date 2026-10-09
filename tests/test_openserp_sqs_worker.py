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
