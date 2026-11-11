"""Every book-bound Grok request carries its book's BookID and its own RequestID, and an answer that
does not echo both is not trusted (owner, 2026-11-11). The trigger: batch answers stored under each
other's cache keys - all from ONE book, so a BookID alone would not have caught it.

Also pins the context fix: a worker thread (run_in_executor) used to start with an empty context,
so `current_book` was lost and every book-scoped cache lookup missed."""

import asyncio
import contextvars
import json
from unittest import mock

import pytest

import src.grok_client as gc
from src.utils import request_identity as ri
from src.utils.cache_backend import DiskCacheBackend
from src.utils.deterministic_ids import book_id


@pytest.fixture(autouse=True)
def _no_book():
    """Other tests set current_book in this thread's context without resetting it."""
    token = gc.current_book.set(None)
    yield
    gc.current_book.reset(token)


@pytest.fixture
def grok(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    return gc.GrokClient(DiskCacheBackend(tmp_path / "c"))


def _reply(body):
    return {"choices": [{"message": {"content": json.dumps(body)}}]}


def _ids(prompt="P", system="SYS", kind="events", book="B", basis=None):
    return ri.stamp(prompt, system, kind, book, basis)[1:]


# --- request_identity -----------------------------------------------------------------------------


def test_stamp_names_the_book_and_the_request():
    stamped, bid, rid = ri.stamp("P", "SYS", "events", "USA-E-XChannel")
    assert bid == book_id("USA-E-XChannel") == "57SJMAQBZYEWDEHACZVT8EZ7AP"
    assert stamped.startswith("P") and bid in stamped and rid in stamped
    assert len(rid) == 16


def test_stamp_is_deterministic_and_idempotent():
    a = ri.stamp("P", "SYS", "events", "B")
    assert ri.stamp("P", "SYS", "events", "B") == a
    assert ri.stamp(a[0], "SYS", "events", "B") == a  # restamping changes nothing


def test_each_request_of_one_book_has_its_own_request_id():
    assert _ids("P1")[1] != _ids("P2")[1]
    assert _ids("P", system="S1")[1] != _ids("P", system="S2")[1]
    assert _ids(kind="events")[1] != _ids(kind="people")[1]
    assert _ids(book="B1")[0] != _ids(book="B2")[0]


def test_text_quoting_the_marker_is_neither_taken_for_a_block_nor_cut():
    quoted = (
        'chapter text ANSWER IDENTITY (required): "RequestID": "0123456789abcdef" more'
    )
    prompt = "P\n\n" + quoted.replace("ANSWER", "\n\nANSWER", 1)
    stamped, _, rid = ri.stamp(prompt, "SYS", "events", "B")
    assert stamped.startswith(prompt)  # nothing cut
    assert rid != "0123456789abcdef" and stamped.endswith(ri._block(book_id("B"), rid))


def test_a_stable_key_names_the_request_whatever_its_prompt_text():
    """supplemental's prompt text varies between a batch submit and the retrieve re-run; its
    stable cache key does not - nor may its RequestID, or the batch answer would never match.
    """
    assert _ids("P1", basis="K")[1] == _ids("P2", basis="K")[1]


@pytest.mark.parametrize(
    "answer, status",
    [
        ({"x": 1, "BookID": "b", "RequestID": "r"}, "ok"),
        ({"x": 1, "BookID": "b", "RequestID": "other"}, "mismatch"),
        ({"x": 1, "BookID": "other", "RequestID": "r"}, "mismatch"),
        ({"x": 1, "RequestID": "r"}, "missing"),  # a partial echo: unproven, not wrong
        ({"x": 1, "RequestID": "other"}, "mismatch"),
        ({"x": 1}, "missing"),
    ],
)
def test_verify_strips_the_ids_and_reports(answer, status):
    got, st = ri.verify(dict(answer), "b", "r")
    assert st == status and got == {"x": 1}


def test_an_array_answer_is_unverifiable():
    assert ri.verify([1], "b", "r") == ([1], "unverifiable")


def test_only_object_kinds_read_by_extract_json_are_bound():
    assert ri.is_bound("events", "B") and not ri.is_bound("events", None)
    for kind in ("equipment_broad", "casualties", "biography", "places_enrichment"):
        assert not ri.is_bound(kind, "B")


# --- GrokClient.extract_json ----------------------------------------------------------------------


def test_the_prompt_sent_carries_the_identity_and_the_answer_is_returned_without_it(
    grok,
):
    bid, rid = _ids()
    with mock.patch.object(
        grok,
        "_call_api",
        return_value=_reply({"x": 1, "BookID": bid, "RequestID": rid}),
    ) as api:
        got = grok.extract_json("P", "SYS", cache_type="events", book="B")
    assert got == {"x": 1}
    sent = api.call_args.args[0][-1]["content"]
    assert bid in sent and rid in sent
    # cached under the stamped request, and a re-read verifies too
    with mock.patch.object(grok, "_call_api") as api:
        assert grok.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 1}
    api.assert_not_called()


def test_a_cached_answer_to_another_request_is_evicted_and_asked_again(grok):
    """The 2026-11-11 bug: request A's cache entry held request C's answer."""
    bid, rid = _ids()
    key = grok.cache_key_for("P", "SYS", "events", book="B")
    with gc.book_scope("B"):
        grok._get_cache("events")[key] = json.dumps(
            {"x": "C", "BookID": bid, "RequestID": "0" * 16}
        )
    good = _reply({"x": "A", "BookID": bid, "RequestID": rid})
    with (
        mock.patch.object(grok, "_call_api", return_value=good) as api,
        mock.patch.object(gc, "observe_warn") as warn,
    ):
        assert grok.extract_json("P", "SYS", cache_type="events", book="B") == {
            "x": "A"
        }
    assert api.call_count == 1
    assert warn.call_args.args[:2] == ("grok", "identity_mismatch")


def test_a_second_mismatch_raises(grok):
    bid, _ = _ids()
    wrong = _reply({"x": 1, "BookID": bid, "RequestID": "0" * 16})
    with (
        mock.patch.object(grok, "_call_api", return_value=wrong) as api,
        mock.patch.object(gc, "observe_error") as err,
    ):
        with pytest.raises(gc.GrokAPIError, match="identity mismatch"):
            grok.extract_json("P", "SYS", cache_type="events", book="B")
    assert api.call_count == 2
    assert err.call_args.args[:2] == ("grok", "identity_mismatch")


def test_an_answer_from_another_book_is_a_mismatch(grok):
    _, rid = _ids()
    other = _reply({"x": 1, "BookID": book_id("OtherBook"), "RequestID": rid})
    with (
        mock.patch.object(grok, "_call_api", return_value=other),
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokAPIError):
            grok.extract_json("P", "SYS", cache_type="events", book="B")


def test_a_missing_identity_is_asked_once_more_then_accepted_with_a_warning(grok):
    with (
        mock.patch.object(grok, "_call_api", return_value=_reply({"x": 1})) as api,
        mock.patch.object(gc, "observe_warn") as warn,
    ):
        assert grok.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 1}
    assert api.call_count == 2
    assert [c.args[1] for c in warn.call_args_list] == ["identity_missing"] * 2
    assert warn.call_args.kwargs["action"] == "accept"


def test_a_missing_identity_answered_on_the_re_ask_is_ok(grok):
    bid, rid = _ids()
    answers = [_reply({"x": 1}), _reply({"x": 2, "BookID": bid, "RequestID": rid})]
    with mock.patch.object(grok, "_call_api", side_effect=answers):
        assert grok.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 2}


def test_an_unbound_kind_or_no_book_is_sent_unchanged(grok):
    with mock.patch.object(grok, "_call_api", return_value=_reply({"x": 1})) as api:
        grok.extract_json("P", "SYS", cache_type="biography", book="B")
        grok.extract_json("Q", "SYS", cache_type="events")  # no book
    assert [c.args[0][-1]["content"] for c in api.call_args_list] == ["P", "Q"]


def test_the_explicit_book_scopes_the_cache(grok):
    bid, rid = _ids()
    with mock.patch.object(
        grok,
        "_call_api",
        return_value=_reply({"x": 1, "BookID": bid, "RequestID": rid}),
    ):
        grok.extract_json("P", "SYS", cache_type="events", book="B")
    assert "books/B/events" in grok.caches
    assert gc.current_book.get() is None  # the scope does not leak


def test_a_stable_cache_key_still_verifies_across_prompt_text(grok):
    """supplemental: submit and re-run share the stable key but not the prompt text."""
    bid, rid = _ids("P1", kind="supplemental", basis="K")
    with mock.patch.object(
        grok,
        "_call_api",
        return_value=_reply({"x": 1, "BookID": bid, "RequestID": rid}),
    ):
        grok.extract_json(
            "P1", "SYS", cache_type="supplemental", cache_key="K", book="B"
        )
    with mock.patch.object(grok, "_call_api") as api:
        got = grok.extract_json(
            "P2", "SYS", cache_type="supplemental", cache_key="K", book="B"
        )
    api.assert_not_called()
    assert got == {"x": 1}


# --- worker-thread context (fix 1) ----------------------------------------------------------------


def test_a_bare_executor_thread_loses_the_book_and_a_copied_context_keeps_it():
    async def run():
        gc.current_book.set("B")
        loop = asyncio.get_running_loop()
        bare = await loop.run_in_executor(None, gc.current_book.get)
        ctx = contextvars.copy_context()
        kept = await loop.run_in_executor(None, lambda: ctx.run(gc.current_book.get))
        return bare, kept

    bare, kept = asyncio.run(run())
    assert kept == "B"
    assert bare in (
        None,
        "B",
    )  # documents the hazard: not guaranteed - never rely on it


def test_batch_parallel_asks_for_the_chapters_book_from_its_worker_thread(tmp_path):
    """_batch_extract hops to a worker thread for the call; the book must arrive."""
    from src.extraction import batch_parallel as bp

    seen = {}

    class Client:
        def extract_json(self, prompt, cache_type="default", book=None, **kw):
            seen["book"], seen["ctx"] = book, gc.current_book.get()
            raise gc.BatchModeCollecting("queued")

    async def run():
        gc.current_book.set("USA-E-XChannel")
        return await bp._batch_extract(
            {"Event": {"EventID": "E", "Sub-events": [{"Sub-eventID": "S"}]}},
            Client(),
            tmp_path,
            "people",
            "people",
            "H",
            "People",
            "x",
            mock.Mock(),
            mock.Mock(),
            book_meta={"book": "USA-E-XChannel"},
        )

    assert asyncio.run(run()) == {"count": 0, "links": {}}
    assert seen == {"book": "USA-E-XChannel", "ctx": "USA-E-XChannel"}


# --- an unproven answer is settled once, not re-asked every run ---------------------------------


def test_an_accepted_missing_answer_is_kept_stamped_so_a_re_run_does_not_ask_again(
    grok,
):
    with mock.patch.object(grok, "_call_api", return_value=_reply({"x": 1})) as api:
        grok.extract_json("P", "SYS", cache_type="events", book="B")
        assert grok.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 1}
    assert (
        api.call_count == 2
    )  # the first run's answer + one re-ask; the re-run is a cache hit


# --- batch mode: a paid answer is not thrown away ------------------------------------------------


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    return gc.GrokClient(DiskCacheBackend(tmp_path / "b"), batch_mode=True)


def _retrieved(client, body, prompt="P"):
    """What a batch retrieval leaves: the answer under the key the request was queued with."""
    with pytest.raises(gc.BatchModeCollecting):
        client.extract_json(prompt, "SYS", cache_type="events", book="B")
    queued = client._batch_collector.requests[-1]
    with gc.book_scope("B"):
        client._get_cache("events")[queued.request_id] = json.dumps(body)
    return queued


def test_the_queued_key_is_the_key_the_re_run_reads(batch):
    bid, rid = _ids()
    queued = _retrieved(batch, {"x": 1, "BookID": bid, "RequestID": rid})
    assert queued.request_id == batch.cache_key_for("P", "SYS", "events", book="B")
    assert rid in queued.messages[-1]["content"]
    assert batch.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 1}


def test_in_batch_mode_a_missing_echo_is_accepted_not_re_queued(batch):
    _retrieved(batch, {"x": 1})
    n = len(batch._batch_collector.requests)
    assert batch.extract_json("P", "SYS", cache_type="events", book="B") == {"x": 1}
    assert len(batch._batch_collector.requests) == n


def test_in_batch_mode_a_mismatch_is_re_queued_once_then_raises(batch):
    bid, _ = _ids()
    wrong = {"x": 1, "BookID": bid, "RequestID": "0" * 16}
    _retrieved(batch, wrong)
    with pytest.raises(gc.BatchModeCollecting):  # evicted + re-queued
        batch.extract_json("P", "SYS", cache_type="events", book="B")
    key = batch.cache_key_for("P", "SYS", "events", book="B")
    with gc.book_scope("B"):
        batch._get_cache("events")[key] = json.dumps(
            wrong
        )  # the next batch is wrong again
    with mock.patch.object(gc, "observe_error"), pytest.raises(gc.GrokAPIError):
        batch.extract_json("P", "SYS", cache_type="events", book="B")
    with gc.book_scope("B"):
        assert key not in batch._get_cache(
            "events"
        )  # the wrong answer is not left cached
