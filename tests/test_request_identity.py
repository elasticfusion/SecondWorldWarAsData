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


def test_the_chapter_kinds_are_bound_and_cross_book_enrichment_is_not():
    assert ri.is_bound("events", "B") and not ri.is_bound("events", None)
    for kind in ("casualties", "people_groups", "equipment_broad", "equipment_refine"):
        assert ri.is_bound(kind, "B")
    for kind in ("biography", "places_enrichment", "date_summary"):
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


# --- every chapter extractor is verified, for the book of the file it processes -----------------


def _stamped_reply(prompt_kind, body, book="B", prompt=None, system=None, temp=None):
    """A fake _call_api that echoes the identity the request carried."""

    def call(messages, *a, **k):
        import re

        sent = messages[-1]["content"]
        bid = re.search(r'"BookID": "([0-9A-Z]{26})"', sent).group(1)
        rid = re.search(r'"RequestID": "([0-9a-f]{16})"', sent).group(1)
        return _reply({**body, "BookID": bid, "RequestID": rid})

    return call


def test_casualties_are_read_through_the_identity_check(grok):
    from src.extraction import casualties as C

    body = {"S1": [{"description": "x"}]}
    with (
        gc.book_scope("B"),
        mock.patch.object(
            grok, "_call_api", side_effect=_stamped_reply("casualties", body)
        ) as api,
        mock.patch.object(C, "_validate_items", side_effect=lambda items: items),
    ):
        got = C._call_and_parse_casualties(grok, "P")
    assert got == {"S1": [{"description": "x"}]}
    assert '"RequestID"' in api.call_args.args[0][-1]["content"]


def test_people_groups_are_read_through_the_identity_check(grok, tmp_path):
    from src.extraction import people_groups as G

    body = {"People_Groups": [{"name": "1st Infantry Division"}]}
    with (
        gc.book_scope("B"),
        mock.patch.object(
            grok, "_call_api", side_effect=_stamped_reply("people_groups", body)
        ),
    ):
        got = G._call_groups_grok("P", tmp_path / "e.json", {}, grok)
    assert G._parse_groups_response(got) == [{"name": "1st Infantry Division"}]


def test_people_groups_never_read_another_requests_answer_as_no_groups(grok, tmp_path):
    from src.extraction import people_groups as G

    bid, _ = _ids(kind="people_groups")
    wrong = _reply({"People_Groups": [], "BookID": bid, "RequestID": "0" * 16})
    with (
        gc.book_scope("B"),
        mock.patch.object(grok, "_call_api", return_value=wrong),
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokIdentityError):
            G._call_groups_grok("P", tmp_path / "e.json", {}, grok)


def test_an_unparsable_groups_answer_is_a_parse_failure_not_a_truncation(
    grok, tmp_path
):
    """extract_people_groups' parse-error path takes it (a loud drop; tests/
    test_core_extractor_observ) - not mislabelled "truncated - event too large"."""
    from src.extraction import people_groups as G

    junk = {"choices": [{"message": {"content": "no JSON here " * 60}}]}
    with (
        mock.patch.object(grok, "_call_api", return_value=junk),
        mock.patch.object(gc, "observe_error"),
        mock.patch.object(G, "observe_error") as err,
    ):
        with pytest.raises(gc.GrokParseError):
            G._call_groups_grok("P", tmp_path / "e.json", {}, grok)
    err.assert_not_called()


def test_an_extractor_runs_for_the_book_of_its_file(tmp_path):
    seen = []

    @gc.for_chapter_book
    def extract(event_file, *a):
        seen.append(gc.current_book.get())

    token = gc.current_book.set("SomeOtherBook")  # left behind by an earlier chapter
    try:
        extract(tmp_path / "USA-E-XChannel" / "chapter8-event.json")
        extract(event_file=tmp_path / "B2" / "chapter1-event.json")
        extract(tmp_path / "Bad Name!" / "chapter1-event.json")  # no safe slug: no book
        # a caller's scratch folder: the caller's book
        extract(tmp_path / "_supplemental_tmp" / "x-event.json")
        extract(tmp_path / "_Foo" / "chapter1-event.json")  # a slug may start with "_"
        assert gc.current_book.get() == "SomeOtherBook"  # restored
    finally:
        gc.current_book.reset(token)
    assert seen == ["USA-E-XChannel", "B2", None, "SomeOtherBook", "_Foo"]


@pytest.mark.parametrize(
    "module, name",
    [
        ("logistics", "extract_logistics_from_event"),
        ("weather_central", "extract_weather_central"),
        ("supplemental", "extract_supplemental"),
        ("casualties", "extract_casualties"),
        ("equipment", "extract_equipment_from_event"),
        ("people_groups", "extract_people_groups"),
        ("source_section", "_enrich_one_section"),
    ],
)
def test_every_chapter_extractor_names_its_files_book(module, name):
    import importlib
    import inspect

    fn = getattr(importlib.import_module(f"src.extraction.{module}"), name)
    assert getattr(fn, "__wrapped__", None) is not None  # @for_chapter_book
    assert next(iter(inspect.signature(fn).parameters)) == "event_file"


def test_a_prose_answer_is_a_parse_failure_not_a_truncation(grok):
    """A truncation sends the chunk to the halver, which re-asks it down to single items: a prose
    answer ("there are no casualties here") must not, or one chunk costs ~2(2n-1) calls.
    """
    prose = {
        "choices": [{"message": {"content": "There are no casualties here. " * 30}}]
    }
    with (
        mock.patch.object(grok, "_call_api", return_value=prose),
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokNoJsonError):
            grok.extract_json("P", cache_type="casualties")
    cited = {"choices": [{"message": {"content": "No casualties [see note 1]. " * 30}}]}
    with (
        mock.patch.object(grok, "_call_api", return_value=cited),
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokNoJsonError):  # a bracket in prose is still prose
            grok.extract_json("Q", cache_type="casualties")


def test_a_malformed_json_answer_is_retried_by_casualties(grok):
    """Only a no-JSON answer is final; a malformed JSON one is asked again (a fresh one often
    parses)."""
    from src.extraction import casualties as C

    bad = {  # "Extra data": malformed JSON, not a truncation
        "choices": [{"message": {"content": '{"S1": []} ' + "x " * 300}}]
    }
    good = _stamped_reply("casualties", {"S1": []})
    answers = iter([bad, bad])

    def call(messages, *a, **k):
        nxt = next(answers, None)
        return nxt if nxt is not None else good(messages)

    with (
        gc.book_scope("B"),
        mock.patch.object(grok, "_call_api", side_effect=call),
        mock.patch.object(gc, "observe_error"),
        mock.patch.object(grok, "_try_repair_json", return_value=None),
    ):
        assert C._call_and_parse_casualties(grok, "P") == {"S1": []}


def test_casualties_do_not_re_ask_a_prose_answer(grok):
    from src.extraction import casualties as C

    prose = {
        "choices": [{"message": {"content": "There are no casualties here. " * 30}}]
    }
    with (
        mock.patch.object(grok, "_call_api", return_value=prose) as api,
        mock.patch.object(gc, "observe_error"),
        mock.patch.object(C, "observe_error") as err,
    ):
        assert C._call_and_parse_casualties(grok, "P") == {}
    assert api.call_count == 2  # the answer + the client's one poisoned-body re-ask
    assert err.call_args.kwargs["reason"] == "no_json_object"


def test_casualties_raise_on_another_requests_answer(grok):
    from src.extraction import casualties as C

    bid, _ = _ids(kind="casualties")
    wrong = _reply({"S1": [], "BookID": bid, "RequestID": "0" * 16})
    with (
        gc.book_scope("B"),
        mock.patch.object(grok, "_call_api", return_value=wrong) as api,
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokIdentityError):
            C._call_and_parse_casualties(grok, "P")
    assert api.call_count == 2  # not retried by the extractor
