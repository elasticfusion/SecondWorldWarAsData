"""Task 1.7 (Group 2): every Phase 3 request carries its RequestID (and BookID where it is about one
document) and an answer that does not echo it is not trusted; a Phase 3 batch answer is paired with
its request ONLY by the id the batch returned; the batch of any cached answer can be found.
"""

import json
from unittest import mock

import pytest

import src.grok_client as gc
from src.utils import request_identity as ri
from src.utils.answer_provenance import batches_for
from src.utils.batch_api import (
    RESULT_UNALIGNABLE,
    BatchResult,
    align_by_request_id,
    strict_alignment,
)
from src.utils.cache_backend import DiskCacheBackend
from src.utils.deterministic_ids import book_id


@pytest.fixture(autouse=True)
def _no_book():
    token = gc.current_book.set(None)
    yield
    gc.current_book.reset(token)


@pytest.fixture
def grok(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    return gc.GrokClient(DiskCacheBackend(tmp_path / "c"))


def _rid(grok, kind, prompt="P", system=None, temperature=0.1):
    """The RequestID extract_json gives a request-only kind: derived from its (unstamped) cache key."""
    key = grok._make_cache_key(prompt, temperature, grok._get_model(kind), system or "")
    return ri.stamp(prompt, system, kind, None, key)[2]


def _reply(body):
    return {"choices": [{"message": {"content": json.dumps(body)}}]}


# --- the kinds ------------------------------------------------------------------------------------


def test_every_phase3_json_kind_carries_a_request_id_and_chapter_kinds_are_unchanged():
    for kind in (
        "group_enrichment",
        "place_enrichment",
        "equipment_disambiguation",
        "license_check",
        "openserp_verify",
        "people_group_source_recheck",
        "equipment_source_recheck",
        "unit_patch_vision",
        "vision_verification",
        "external_maps_verification",
        "in_doc_map_vision",
    ):
        assert ri.is_request_bound(kind), kind
    # a one-word answer cannot echo: its proof is the id the batch returns (strict alignment)
    for kind in ("bibliography_verify", "link_relevancy", "places", "default"):
        assert not ri.is_request_bound(kind), kind
    assert ri.is_bound("events", "B") and not ri.is_bound("events", None)
    assert ri.is_bound("in_doc_map_vision", "B") and not ri.is_bound(
        "in_doc_map_vision", None
    )
    assert not ri.is_bound("group_enrichment", "B")  # about no single book


def test_a_request_only_stamp_names_the_request_not_a_book():
    stamped, bid, rid = ri.stamp("P", "S", "group_enrichment", None)
    assert bid == "" and len(rid) == 16 and rid in stamped and "BookID" not in stamped
    assert ri.stamp(stamped, "S", "group_enrichment", None) == (stamped, "", rid)
    assert ri.stamp("P", "S", "place_enrichment", None)[2] != rid  # per kind
    assert ri.stamp("P2", "S", "group_enrichment", None)[2] != rid  # per request


@pytest.mark.parametrize(
    "answer, status",
    [
        ({"x": 1, "RequestID": "r"}, "ok"),
        ({"x": 1, "RequestID": "other"}, "mismatch"),
        ({"x": 1}, "missing"),
    ],
)
def test_request_only_verify(answer, status):
    got, st = ri.verify(dict(answer), "", "r")
    assert st == status and got == {"x": 1}


# --- extract_json ----------------------------------------------------------------------------------


def test_a_phase3_request_is_sent_with_its_id_and_the_answer_returned_without_it(grok):
    rid = _rid(grok, "group_enrichment")
    with mock.patch.object(
        grok, "_call_api", return_value=_reply({"unit": "3rd", "RequestID": rid})
    ) as api:
        got = grok.extract_json("P", cache_type="group_enrichment")
    assert got == {"unit": "3rd"}
    assert rid in api.call_args.args[0][-1]["content"]
    with mock.patch.object(grok, "_call_api") as api:  # a re-read verifies too
        assert grok.extract_json("P", cache_type="group_enrichment") == {"unit": "3rd"}
    api.assert_not_called()


def test_a_phase3_answer_that_belongs_to_another_request_is_never_used(grok):
    wrong = _reply({"unit": "x", "RequestID": "0" * 16})
    with (
        mock.patch.object(grok, "_call_api", return_value=wrong) as api,
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokAPIError, match="identity mismatch"):
            grok.extract_json("P", cache_type="place_enrichment")
    assert api.call_count == 2  # asked once more, then refused


def test_a_phase3_answer_without_an_echo_is_accepted_at_once_without_a_second_paid_call(
    grok,
):
    with (
        mock.patch.object(
            grok, "_call_api", return_value=_reply({"unit": "3rd"})
        ) as api,
        mock.patch.object(gc, "observe_warn") as warn,
    ):
        assert grok.extract_json("P", cache_type="group_enrichment") == {"unit": "3rd"}
    assert api.call_count == 1  # not doubled
    assert warn.call_args.args[:2] == ("grok", "identity_missing")
    with mock.patch.object(grok, "_call_api") as api:  # kept stamped: the re-read is ok
        assert grok.extract_json("P", cache_type="group_enrichment") == {"unit": "3rd"}
    api.assert_not_called()


def test_a_book_in_scope_does_not_make_a_cross_book_kind_book_bound(grok):
    with gc.book_scope("BookA"):
        stamped = grok._identity("P", None, "group_enrichment", None)
    assert stamped[1] == "" and stamped[2]


def test_the_cache_key_of_a_phase3_kind_names_its_identity(grok):
    """clear_cache_entry / evict go through the same key, so an eviction finds the entry."""
    with mock.patch.object(
        grok,
        "_call_api",
        return_value=_reply({"a": 1, "RequestID": _rid(grok, "license_check")}),
    ):
        grok.extract_json("P", cache_type="license_check")
    assert grok.clear_cache_entry("P", "license_check") is True
    assert grok.clear_cache_entry("P", "license_check") is False


# --- vision ----------------------------------------------------------------------------------------

PNG = "iVBORw0KGgo="


def test_a_vision_request_carries_its_ids_and_an_echo_is_required(grok):
    with gc.book_scope("BookA"):
        # the key basis of a document-bound kind names the document (cached per document)
        _, bid, rid = grok._identity(
            "is it a map?", None, "in_doc_map_vision", None, "k|book:BookA"
        )
    assert bid == book_id("BookA")
    grok._detect_image_type = lambda b: "image/png"  # type: ignore[method-assign]
    answer = _reply({"is_relevant": True, "BookID": bid, "RequestID": rid})
    with (
        gc.book_scope("BookA"),
        mock.patch.object(grok, "_call_api", return_value=answer) as api,
    ):
        got = grok._vision_json(
            "is it a map?",
            "k",
            lambda: "data:image/png;base64,AA",
            None,
            0.1,
            True,
            "in_doc_map_vision",
        )
    assert got == {"is_relevant": True}
    text = api.call_args.args[0][-1]["content"][0]["text"]
    assert bid in text and rid in text


def test_a_vision_answer_for_another_request_is_evicted_and_refused(grok):
    key_basis = "is it a map?:https://x.org/m.png"
    wrong = _reply({"is_relevant": True, "RequestID": "0" * 16})
    with (
        mock.patch.object(grok, "_call_api", return_value=wrong),
        mock.patch.object(gc, "observe_error"),
    ):
        with pytest.raises(gc.GrokIdentityError):
            grok._vision_json(
                "is it a map?",
                key_basis,
                lambda: "data:image/png;base64,AA",
                None,
                0.1,
                True,
                "unit_patch_vision",
            )
    cache = grok._get_cache("unit_patch_vision")
    assert (
        grok._make_cache_key(key_basis, 0.1, grok._get_model("unit_patch_vision"))
        not in cache
    )


def test_a_batch_vision_answer_is_proven_when_read(grok):
    """The batch caches the answer as TEXT under the request id; the read proves it."""
    key_basis = "q:img"
    _, _, rid = ri.stamp("q", None, "vision_verification", None, key_basis)
    key = grok._make_cache_key(key_basis, 0.1, grok._get_model("vision_verification"))
    grok._get_cache("vision_verification")[key] = json.dumps(
        {"is_relevant": True, "RequestID": rid}
    )
    got = grok._vision_json(
        "q",
        key_basis,
        lambda: pytest.fail("no fetch"),
        None,
        0.1,
        True,
        "vision_verification",
    )
    assert got == {"is_relevant": True}
    grok._get_cache("vision_verification")[key] = json.dumps(
        {"is_relevant": True, "RequestID": "f" * 16}
    )
    with mock.patch.object(gc, "observe_error"), pytest.raises(gc.GrokIdentityError):
        grok._vision_json(
            "q",
            key_basis,
            lambda: pytest.fail("no fetch"),
            None,
            0.1,
            True,
            "vision_verification",
        )


def test_a_vision_kind_without_identity_is_unchanged(grok):
    answer = _reply({"description": "d"})
    with mock.patch.object(grok, "_call_api", return_value=answer) as api:
        got = grok._vision_json(
            "caption",
            "c:img",
            lambda: "data:image/png;base64,AA",
            None,
            0.1,
            True,
            "default",
        )
    assert got == {"description": "d"}
    assert api.call_args.args[0][-1]["content"][0]["text"] == "caption"


# --- strict alignment -----------------------------------------------------------------------------


SUBMITTED = [("k1", "t"), ("k2", "t")]


def _results(*ids):
    return [BatchResult(request_id=i, content="x", finish_reason="stop") for i in ids]


def test_phase3_refuses_to_pair_answers_by_position(caplog):
    server_ids = _results("srv-1", "srv-2")  # none carries one of our ids
    with mock.patch("src.utils.observe.observe_error") as err:
        out = align_by_request_id(SUBMITTED, server_ids, batch_id="b", strict=True)
    assert [r.error for r in out] == [RESULT_UNALIGNABLE] * 2
    assert [r.request_id for r in out] == ["k1", "k2"]
    assert err.call_args.args[1] == "align_positional_refused"
    # Phase 2 keeps the old behaviour
    assert align_by_request_id(SUBMITTED, server_ids, batch_id="b") == server_ids


def test_strict_alignment_still_pairs_by_id_in_any_order():
    out = align_by_request_id(
        SUBMITTED, _results("k2", "k1"), batch_id="b", strict=True
    )
    assert [r.request_id for r in out] == ["k1", "k2"] and all(r.ok for r in out)


def test_which_phases_are_strict(monkeypatch):
    assert strict_alignment("phase3_enrich_data.py") and strict_alignment(
        "phase3_enrich_data"
    )
    assert not strict_alignment("phase2_extract.py")
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
    assert strict_alignment()
    monkeypatch.setenv("PIPELINE_PHASE", "phase2_extract")
    assert not strict_alignment()


def test_the_retrieve_path_is_strict_for_phase3(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "t")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import ecs_entrypoint as ep

    with mock.patch("src.utils.observe.observe_error"):
        assert (
            ep._map_batch_results_by_index(
                SUBMITTED, _results("srv-1", "srv-2"), "b", strict=True
            )
            == {}
        )
    positional = ep._map_batch_results_by_index(
        SUBMITTED, _results("srv-1", "srv-2"), "b"
    )
    assert set(positional.get("t", {})) == {"k1", "k2"}  # Phase 2: as before


def test_an_in_task_phase3_batch_with_unproven_answers_is_not_retried_live(
    grok, tmp_path, monkeypatch
):
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
    monkeypatch.chdir(tmp_path)
    grok.cache_dir = tmp_path
    grok._config = {"batch": {"min_requests": 1}}
    grok.batch_mode = True
    from src.utils.batch_api import BatchCollector, BatchRequest

    grok._batch_collector = BatchCollector()
    for k in ("k1", "k2"):
        grok._batch_collector.add(
            BatchRequest(k, [{"role": "user", "content": k}], "m", 0.1, "t")
        )
    live = mock.Mock()
    with (
        mock.patch.object(
            grok,
            "_submit_poll_retrieve",
            return_value=("b1", {"state": {}}, _results("srv-1", "srv-2")),
        ),
        mock.patch.object(grok, "_retry_failed_batch", live),
        mock.patch.object(grok, "_record_batch_submitted"),
        mock.patch("src.utils.credit_gate.reserve", return_value=True),
        mock.patch("src.utils.observe.observe_error"),
        mock.patch("src.utils.job_queue.mark_retrieved_in_task"),
        mock.patch.object(gc, "_save_metrics"),
    ):
        grok.submit_batch("n")
    live.assert_not_called()  # every Grok submission is batched: the next round asks again


# --- provenance ledger ----------------------------------------------------------------------------


def test_the_batch_of_a_cached_answer_is_found_from_the_metrics(tmp_path):
    (tmp_path / "batch_b1_100.json").write_text(
        json.dumps(
            {
                "batch_id": "b1",
                "batch_name": "phase3-BookA-r1-2reqs",
                "submitted_at": 1000,
                "served_models": {"grok-4.3": 2},
                "submitted_request_ids": [
                    ["k1", "group_enrichment"],
                    ["k2", "place_enrichment"],
                ],
            }
        )
    )
    (tmp_path / "batch_b2_200.json").write_text(
        json.dumps(
            {
                "batch_id": "b2",
                "batch_name": "phase3-BookA-r2-1reqs",
                "submitted_at": 2000,
                "submitted_request_ids": [["k1", "group_enrichment"]],
            }
        )
    )
    (tmp_path / "batch_bad_1.json").write_text("{not json")
    found = batches_for("k1", tmp_path)
    assert [(b["batch_id"], b["round"]) for b in found] == [("b1", 1), ("b2", 2)]
    assert (
        found[0]["served_models"] == {"grok-4.3": 2}
        and found[0]["cache_type"] == "group_enrichment"
    )
    assert batches_for("k2", tmp_path)[0]["batch_id"] == "b1"
    assert batches_for("nope", tmp_path) == []


def test_the_trace_script_prints_the_batch(tmp_path, capsys):
    from scripts import trace_grok_answer as T

    (tmp_path / "batch_b1_100.json").write_text(
        json.dumps(
            {
                "batch_id": "b1",
                "batch_name": "phase3-B-r3-1reqs",
                "submitted_at": 1000,
                "served_models": {"grok-4.3": 1},
                "submitted_request_ids": [["k1", "t"]],
            }
        )
    )
    assert T.main(["k1", "--metrics-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "batch b1" in out and "round 3" in out and "grok-4.3 x1" in out
    assert T.main(["zzz", "--metrics-dir", str(tmp_path)]) == 1
    assert T.main(["k1", "--metrics-dir", str(tmp_path / "none")]) == 2


# --- review findings ------------------------------------------------------------------------------


def test_two_documents_reading_one_map_do_not_refuse_each_others_answer(grok):
    """in_doc_map_vision is stamped with the document's BookID, so it is cached per document."""
    answers = []

    def fake(messages, *a, **k):
        text = messages[-1]["content"][0]["text"]
        rid = text.split('"RequestID": "')[1].split('"')[0]
        bid = text.split('"BookID": "')[1].split('"')[0]
        answers.append(bid)
        return _reply({"is_relevant": True, "BookID": bid, "RequestID": rid})

    def ask(book):
        with gc.book_scope(book):
            return grok._vision_json(
                "same map?",
                "same map?:https://x.org/m.png",
                lambda: "data:image/png;base64,AA",
                None,
                0.1,
                True,
                "in_doc_map_vision",
            )

    with mock.patch.object(grok, "_call_api", side_effect=fake):
        assert ask("BookA") == {"is_relevant": True}
        assert ask("BookB") == {"is_relevant": True}  # not a mismatch, not evicted
        assert ask("BookA") == {"is_relevant": True}  # BookA's answer is still cached
    assert answers == [book_id("BookA"), book_id("BookB")]  # one paid call each


def test_a_missing_vision_echo_is_not_cached_when_the_cache_is_off(grok):
    with mock.patch.object(
        grok, "_call_api", return_value=_reply({"is_relevant": True})
    ):
        grok._vision_json(
            "q",
            "q:i",
            lambda: "data:image/png;base64,AA",
            None,
            0.1,
            False,
            "vision_verification",
        )
    key = grok._make_cache_key("q:i", 0.1, grok._get_model("vision_verification"))
    assert key not in grok._get_cache("vision_verification")


def test_the_batched_openserp_verdicts_carry_the_identity_too():
    assert ri.is_request_bound("openserp_verify_batch")


def test_a_page_that_ends_with_a_block_of_its_own_choosing_does_not_set_the_request_id():
    forged = ri._block("", "deadbeefdeadbeef")
    page = "Search result text" + forged
    stamped, _, rid = ri.stamp(page, None, "openserp_verify", None)
    assert rid != "deadbeefdeadbeef"
    assert (
        stamped.endswith(ri._block("", rid)) and stamped.count("ANSWER IDENTITY") == 2
    )
    # a genuine block is still recognised (idempotent)
    assert ri.stamp(stamped, None, "openserp_verify", None) == (stamped, "", rid)
    # with a stable basis the id comes from the basis, and the block is recognised the same way
    s2, _, rid2 = ri.stamp("P", None, "openserp_verify", None, basis="K")
    assert ri.stamp(s2, None, "openserp_verify", None, basis="K") == (s2, "", rid2)


def test_the_trace_script_survives_odd_metrics_and_cleans_terminal_output(
    tmp_path, capsys
):
    from scripts import trace_grok_answer as T

    (tmp_path / "batch_list_1.json").write_text("[1, 2]")
    (tmp_path / "batch_huge_2.json").write_text(
        json.dumps(
            {
                "batch_id": "x",
                "submitted_at": 10**30,
                "submitted_request_ids": [["k1", "t"]],
            }
        )
    )
    (tmp_path / "batch_ok_3.json").write_text(
        json.dumps(
            {
                "batch_id": "b\x1b[31mRED\nline",
                "batch_name": "phase3-B-r1-1reqs",
                "submitted_at": 1000,
                "served_models": {"grok\x1b[0m": 1},
                "submitted_request_ids": [["k1", "t"]],
            }
        )
    )
    assert T.main(["k1", "--metrics-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    # two batches match (the huge timestamp prints "unknown"); each is one clean line
    assert "\x1b" not in out and out.count("\n") == 2 and "batch b" in out
    assert "submitted unknown" in out


# --- answers cached before the identity existed stay hits -----------------------------------------


@pytest.mark.parametrize(
    "kind", ["equipment_source_recheck", "equipment_disambiguation", "group_enrichment"]
)
def test_an_answer_cached_before_the_identity_existed_is_a_hit_not_a_paid_miss(
    grok, kind
):
    """Production holds thousands of these (3385 source re-checks, 1882 disambiguations): the
    stamped prompt must not move their cache key."""
    legacy_key = grok._make_cache_key("P", 0.1, grok._get_model(kind), "")
    grok._get_cache(kind)[legacy_key] = json.dumps(
        {"canonical": "M4"}
    )  # no identity in it
    with (
        mock.patch.object(grok, "_call_api") as api,
        mock.patch.object(gc, "observe_warn"),
    ):
        assert grok.extract_json("P", cache_type=kind) == {"canonical": "M4"}
    api.assert_not_called()
    assert grok.cache_key_for("P", None, kind, 0.1) == legacy_key
    # it is kept STAMPED now, so the next read is ok (no warning)
    with (
        mock.patch.object(grok, "_call_api") as api,
        mock.patch.object(gc, "observe_warn") as warn,
    ):
        assert grok.extract_json("P", cache_type=kind) == {"canonical": "M4"}
    api.assert_not_called() and warn.assert_not_called()


def test_the_request_id_of_a_request_only_kind_is_stable_across_calls(grok):
    sent = []

    def fake(messages, *a, **k):
        sent.append(messages[-1]["content"])
        return _reply({"x": 1})

    with (
        mock.patch.object(grok, "_call_api", side_effect=fake),
        mock.patch.object(gc, "observe_warn"),
    ):
        grok.extract_json("P", cache_type="license_check", use_cache=False)
        grok.extract_json("P", cache_type="license_check", use_cache=False)
    assert sent[0] == sent[1] and "RequestID" in sent[0]


def test_an_answer_stored_for_a_request_only_kind_reads_back_as_ok(grok):
    """store_json_answer must give the answer the RequestID extract_json will expect."""
    grok.store_json_answer(
        "P", None, "license_check", {"allowed": True}, temperature=0.1
    )
    with (
        mock.patch.object(grok, "_call_api") as api,
        mock.patch.object(gc, "observe_warn") as warn,
        mock.patch.object(gc, "observe_error") as err,
    ):
        got = grok.extract_json("P", cache_type="license_check")
    assert got == {"allowed": True}
    api.assert_not_called()
    warn.assert_not_called()
    err.assert_not_called()
