"""scripts/validate_batch_answers.py: find cached Grok answers stored under another request's key
(owner 2026-11-18). Run against moto, so the table scans and conditional logic are real.
"""

import json
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from src.utils import answer_validation as av
from src.utils.batch_api import BatchResult
from src.utils.deterministic_ids import book_id

K1, K2, K3 = "a1" * 8, "b2" * 8, "c3" * 8


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        yield ddb.create_table(
            TableName="t",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def _put(table, key, response, **extra):
    table.put_item(Item={"cache_key": key, "response": response, **extra})


def _get(table, key):
    return table.get_item(Key={"cache_key": key}).get("Item")


def _echo(book, rid="r"):
    return json.dumps({"x": 1, "BookID": book_id(book), "RequestID": rid})


def test_split_key_reads_the_kind_book_and_request():
    assert av.split_key(f"books/BookA/events#{K1}") == ("events", "BookA", K1)
    assert av.split_key(f"dates#{K1}") == ("dates", "", K1)


@pytest.mark.parametrize(
    "key, response, status",
    [
        (f"books/A/events#{K1}", _echo("A"), "ok"),
        (f"books/A/events#{K2}", _echo("B"), "book_mismatch"),
        (f"books/A/events#{K3}", json.dumps({"x": 1}), "no_echo"),
        (f"books/A/events#{K1}", "not json", "no_echo"),
        (f"dates#{K1}", json.dumps({"BookID": book_id("Z"), "RequestID": "r"}), "ok"),
        (f"group_enrichment#{K1}", json.dumps({"RequestID": "r"}), "ok"),
        (f"geocode_x#{K1}", "text", "other_kind"),
    ],
)
def test_classify_row(key, response, status):
    assert av.classify_row(key, response)[1] == status


def test_check_echo_counts_by_kind_and_finds_the_rows_of_another_book(table):
    _put(table, f"books/A/events#{K1}", _echo("A"))
    _put(table, f"books/A/events#{K2}", _echo("B"))
    _put(table, f"books/A/weather#{K3}", json.dumps({"x": 1}))
    _put(table, f"default#{K1}", "free text")  # not an identity kind: not scanned
    _put(table, "metrics#b1", "{}")
    r = av.check_echo(table)
    assert r.counts == {"ok": 1, "book_mismatch": 1, "no_echo": 1}
    assert r.by_kind["events"] == {"ok": 1, "book_mismatch": 1}
    assert r.bad == [f"books/A/events#{K2}"]
    assert _get(table, f"books/A/events#{K2}")  # reading deletes nothing
    assert "PROVEN WRONG: 1" in av.format_report(r)


def test_evict_removes_only_the_proven_wrong_rows(table):
    _put(table, f"books/A/events#{K1}", _echo("A"))
    _put(table, f"books/A/events#{K2}", _echo("B"))
    r = av.check_echo(table)
    assert av.evict_bad(table, r) == 1
    assert _get(table, f"books/A/events#{K2}") is None and _get(
        table, f"books/A/events#{K1}"
    )


def test_evict_refuses_any_key_that_is_not_a_cached_identity_answer(table):
    for key in (
        "credit#balance",
        "lock#dev-wwii-phase3",
        f"batch_job#{K1}",
        "books/A/events#x",
        f"books/../events#{K1}",
    ):
        _put(table, key, "keep")
    report = av.Report(
        bad=[
            "credit#balance",
            "lock#dev-wwii-phase3",
            f"batch_job#{K1}",
            "books/A/events#x",
            f"books/../events#{K1}",
        ]
    )
    assert av.evict_bad(table, report) == 0
    assert all(
        _get(table, k)
        for k in ("credit#balance", "lock#dev-wwii-phase3", f"batch_job#{K1}")
    )
    assert av.safe_key(f"books/BookA/events#{K1}") and av.safe_key(f"dates#{K1}")


def _metrics(table, batch_id, ids, name="phase2-BookA-3files-r1-2reqs", created=1000):
    _put(
        table,
        f"metrics#{batch_id}",
        json.dumps(
            {"batch_id": batch_id, "batch_name": name, "submitted_request_ids": ids}
        ),
        created_at=created,
    )


def _results(**by_id):
    return [
        BatchResult(request_id=k, content=v, finish_reason="stop")
        for k, v in by_id.items()
    ]


def _check(table, results, **kw):
    return av.check_batches(
        table, "key", before=2000, fetch=lambda key, bid: results, **kw
    )


def test_a_batch_answer_stored_under_another_request_is_found_even_when_results_come_out_of_order(
    table,
):
    _metrics(table, "b1", [[K1, "events"], [K2, "events"], [K3, "people"]])
    _put(table, f"books/BookA/events#{K1}", '{"who": "one"}')
    _put(
        table, f"books/BookA/events#{K2}", '{"who": "one"}'
    )  # K1's answer under K2's key
    _put(table, f"people#{K3}", "plain text")
    r = _check(
        table,
        _results(**{K3: "plain text", K2: '{"who": "two"}', K1: '{"who": "one"}'}),
    )
    assert r.counts == {"same": 2, "swapped": 1}
    assert r.bad == [f"books/BookA/events#{K2}"]


@pytest.mark.parametrize(
    "result, stored",
    [
        (
            ' {"who": "one"} ',
            json.dumps({"who": "one", "BookID": "b", "RequestID": "r"}),
        ),  # restamped
        (
            '```json\n{"who": "one"}\n```',
            json.dumps({"who": "one", "RequestID": "r"}),
        ),  # fenced, then stamped
        (
            '{"path": "C:\\dir"}',
            '{"path": "C:\\\\dir"}',
        ),  # invalid escape, repaired when cached
        (
            '{"is_relevant": true}',
            {"is_relevant": True},
        ),  # a vision answer cached as the parsed dict
        ('{"b": 2, "a": 1}', '{"a": 1, "b": 2}'),  # key order
    ],
)
def test_the_same_answer_stored_in_another_form_is_not_a_swap(table, result, stored):
    _metrics(table, "b1", [[K1, "events"]])
    _put(table, f"books/BookA/events#{K1}", stored)
    r = _check(table, _results(**{K1: result}))
    assert r.counts == {"same": 1} and r.bad == []


def test_unavailable_empty_uncached_and_recent_batches_are_counted_not_flagged(table):
    _metrics(table, "gone", [[K1, "events"]])
    _metrics(table, "empty", [[K2, "events"]])
    _metrics(
        table, "recent", [[K3, "events"]], created=5000
    )  # after the cutoff: skipped
    _put(
        table,
        "metrics#none",
        json.dumps({"batch_id": "none", "submitted_request_ids": []}),
        created_at=1,
    )

    def fetch(key, bid):
        if bid == "gone":
            raise RuntimeError("expired")
        return [BatchResult(request_id=K2, content="", finish_reason="stop")]

    r = av.check_batches(table, "key", before=2000, fetch=fetch)
    assert r.counts == {"batch_unavailable": 1, "no_answer": 1, "batch_no_ids": 1}
    assert r.bad == [] and "INCOMPLETE: 1 batch" in av.format_report(r)


def test_a_forged_metrics_row_cannot_name_another_entitys_key(table):
    """kind / request id / book / batch id come from metrics rows: only a real identity-kind answer's
    key is ever looked up (or deleted)."""
    _put(table, "credit#balance", "100")
    _put(table, "lock#x", "held")
    forged = [["balance", "credit"], [K1, "lock"], [K2, "events"]]
    _metrics(table, "b1", forged, name="phase2-../..-3files-r1-3reqs")
    _put(table, f"events#{K2}", "ours")
    seen = []

    def fetch(key, bid):
        seen.append(bid)
        return _results(balance="0", **{K1: "x", K2: "ours"})

    r = av.check_batches(table, "key", before=2000, fetch=fetch)
    assert r.bad == [] and _get(table, "credit#balance") and _get(table, "lock#x")
    _metrics(table, "../etc/passwd", [[K1, "events"]])  # a batch id that is not an id
    av.check_batches(table, "key", before=2000, fetch=fetch)
    assert "../etc/passwd" not in seen


def test_a_batch_with_no_id_of_ours_is_not_paired_by_position(table):
    _metrics(table, "b1", [[K1, "events"], [K2, "events"]])
    _put(table, f"books/BookA/events#{K1}", "A")
    _put(table, f"books/BookA/events#{K2}", "B")
    with mock.patch("src.utils.observe.observe_error"):
        r = _check(table, _results(srv1="B", srv2="A"))
    assert (
        r.counts == {"no_answer": 2} and r.bad == []
    )  # nothing guessed, nothing evicted


def test_cutoff_dates_and_batch_books():
    assert av.epoch("1970-01-02") == 86400
    assert av.epoch("2026-11-11") == 1794355200
    assert av.book_of_batch("phase3-USA-E-XChannel-3files-r2-9reqs") == "USA-E-XChannel"
    assert av.book_of_batch("weird") == ""


@pytest.fixture
def cli(table, monkeypatch):
    from scripts import validate_batch_answers as S

    monkeypatch.setattr(
        boto3,
        "resource",
        lambda *a, **k: boto3.session.Session().resource(
            "dynamodb", region_name="us-east-1"
        ),
    )
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    return S


def test_the_script_reports_and_exits_nonzero_and_evicts_only_when_confirmed(
    table, cli, monkeypatch, capsys
):
    bad = f"books/A/events#{K2}"
    _put(table, bad, _echo("B"))
    assert cli.main(["--table", "t"]) == 1
    out = capsys.readouterr().out
    assert f"wrong: '{bad}'" in out and _get(table, bad)
    monkeypatch.setattr("builtins.input", lambda prompt="": "0")  # not the number
    assert cli.main(["--table", "t", "--evict"]) == 1
    assert _get(table, bad) and "Nothing evicted" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    assert cli.main(["--table", "t", "--evict"]) == 1
    assert _get(table, bad) is None
    assert cli.main(["--table", "t"]) == 0


def test_the_script_without_a_key_and_with_unreadable_batches_does_not_report_clean(
    table, cli, monkeypatch
):
    assert cli.main(["--table", "t", "--check", "batches"]) == 2
    monkeypatch.setenv("GROK_API_KEY", "k")
    _metrics(table, "b1", [[K1, "events"]], created=1)
    monkeypatch.setattr(
        av,
        "retrieve_results_ordered",
        lambda k, b: (_ for _ in ()).throw(RuntimeError("401")),
    )
    assert (
        cli.main(["--table", "t", "--check", "batches", "--before", "2100-01-01"]) == 3
    )
