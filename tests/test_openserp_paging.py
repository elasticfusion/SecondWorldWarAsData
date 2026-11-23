"""A productive entity's next search goes one result page deeper (owner 2026-11-23, option 3).

The cursor per entity: the same page when the cap stopped the search (its leftovers are queued
next time), one page deeper when the page had relevant pages (new, or queued by an earlier
search) up to ``openserp.max_result_pages``, else back to the first page. A failed or
OpenSERP-errored search does not move it. Only find queries are paged, never image queries.
"""

import pytest

import src.enrichment.openserp_enrichment as oe

PLACE = {"PlaceID": "01HPLACE", "current_name": "Metz", "images": [{"url": "x"}] * 3}


class _Backend:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def put(self, k, v, ttl_days=30):
        self.store[k] = v


@pytest.fixture
def world(monkeypatch, queued_finds, tmp_path):
    backend = _Backend()
    monkeypatch.setattr("src.utils.search_cache._get_backend", lambda: backend)
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {
            "openserp": {
                "results_per_query": 5,
                "max_result_pages": 3,
                "max_web_results_per_entity": 3,
                "max_images_per_entity": 3,
            }
        },
    )
    monkeypatch.setattr(oe, "prefetch_relevance", lambda *a, **k: 0)
    state = {"starts": [], "relevant": set(), "error": False}

    def search(q, url, limit=None, start=0):
        state["starts"].append(start)
        if state["error"]:
            oe._trace_query(q, "error")
            return []
        oe._trace_query(q, "results", 5)
        return [
            {
                "url": f"https://example.org/p{start + i}",
                "title": "Metz",
                "description": "d",
            }
            for i in range(5)
        ]

    def process(url, *a, **k):
        return {"url": url, "summary": "s"} if url in state["relevant"] else None

    monkeypatch.setattr(oe, "_search_openserp", search)
    monkeypatch.setattr(oe, "process_positive_url", process)
    oe.reset_metrics()

    def run():
        state["starts"].clear()
        data = dict(PLACE)
        return oe._search_entity(
            "places",
            None,
            data,
            "Metz",
            tmp_path,
            lambda: oe.enrich_one_place(data, "u", object()),
        )

    yield state, run, backend
    oe.reset_metrics()  # the counters are module state: leave none for the next test


def _cursor(backend):
    vals = [v for k, v in backend.store.items()]
    return vals[0] if vals else None


def test_the_first_search_starts_at_the_first_page(world):
    state, run, backend = world
    run()
    assert set(state["starts"]) == {0}
    assert _cursor(backend) is None  # nothing relevant: nothing stored


def test_a_productive_search_sends_the_next_one_a_page_deeper(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}  # one new find, cap not reached
    run()
    assert _cursor(backend) == "5"
    run()
    assert set(state["starts"]) == {5}  # results 6-10


def test_a_page_whose_finds_were_all_queued_before_also_goes_deeper(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()
    backend.store.clear()  # as if the cursor expired: page 1 again, its find already queued
    state["relevant"] = set()
    run()
    assert _cursor(backend) == "5"


def test_a_search_stopped_by_the_cap_stays_on_its_page(world):
    state, run, backend = world
    state["relevant"] = {
        f"https://example.org/p{i}" for i in range(5)
    }  # 5 relevant, cap 3
    run()
    assert (
        _cursor(backend) is None
    )  # stays on the first page: its 2 leftovers come next
    run()
    assert set(state["starts"]) == {0}


def test_after_the_last_page_the_next_search_starts_over(world):
    state, run, backend = world
    state["relevant"] = {f"https://example.org/p{i}" for i in (1, 6, 11)}
    run()
    run()
    assert _cursor(backend) == "10"
    run()  # page 3 of 3
    assert _cursor(backend) == "0"


def test_a_page_with_nothing_relevant_sends_the_next_search_back_to_the_start(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()
    state["relevant"] = set()
    run()  # page 2 had nothing
    assert _cursor(backend) == "0"


def test_an_openserp_error_keeps_the_cursor(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()
    state["error"] = True
    assert run() is None  # unavailable: not searched
    assert _cursor(backend) == "5"


def test_a_failed_search_keeps_the_cursor(world, monkeypatch):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()

    def fail(*a, **k):
        raise oe.VerifyFailed("no verdict")

    monkeypatch.setattr(oe, "process_positive_url", fail)
    assert run() is None
    assert _cursor(backend) == "5"


def test_image_queries_are_never_paged(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()
    data = {
        "PlaceID": "01HPLACE",
        "current_name": "Metz",
    }  # no images: image queries run
    starts = state["starts"]
    starts.clear()
    trace = oe._new_trace("places", "f", data, "Metz")
    with oe._tracing(trace):
        oe._enrich_place_images(data, ["Metz"], "Metz", "u", None, 3)
    assert starts and set(starts) == {0}


def test_the_start_offset_is_sent_to_openserp_only_when_deeper():
    assert "start" not in oe._query_params("q", 5, 0)
    assert oe._query_params("q", 5, 10)["start"] == "10"


def test_a_corrupt_cursor_starts_at_the_first_page(world):
    state, run, backend = world
    key = oe._page_cursor_key("place#01HPLACE")
    from src.utils.search_cache import _make_key

    backend.store[_make_key("openserp_page", key)] = "-5"
    run()
    assert set(state["starts"]) == {0}


def test_the_sqs_worker_moves_the_cursor_too(monkeypatch):
    import openserp_worker as w
    from tests.test_openserp_sqs_worker import FakeStorage, _patch_enrich

    _patch_enrich(monkeypatch, changed=True)
    seen = []
    monkeypatch.setattr(oe, "commit_paging", lambda trace: seen.append(trace))
    st = FakeStorage(
        {"people/x.json": {"name": "X", "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW"}}
    )
    body = {"entity_type": "people", "entity_path": "people/x.json"}
    assert (
        w.process_message(body, st, "http://localhost:7001", grok_client=object())
        == "done"
    )
    assert len(seen) == 1


def test_every_metric_is_reset_between_runs():
    oe._metric("entities_unavailable")
    oe._metric("a_metric_nobody_listed")
    oe.reset_metrics()
    snap = oe.get_metrics()
    assert snap["entities_unavailable"] == 0 and "a_metric_nobody_listed" not in snap


@pytest.mark.parametrize("raw", ["²", "9" * 5000, "1000000000", "５", "-5", "7", " 5"])
def test_a_bad_cursor_starts_at_the_first_page_and_is_said(world, caplog, raw):
    import logging

    from src.utils import observe
    from src.utils.search_cache import _make_key

    state, run, backend = world
    observe._counts.clear()
    backend.store[_make_key("openserp_page", oe._page_cursor_key("place#01HPLACE"))] = (
        raw
    )
    with caplog.at_level(logging.WARNING):
        run()  # never raises
    assert set(state["starts"]) == {0}
    assert "bad_page_cursor" in caplog.text


def test_a_capped_search_on_a_deeper_page_stays_there(world):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}
    run()  # -> page 2
    state["relevant"] = {f"https://example.org/p{i}" for i in range(5, 10)}
    run()  # page 2: 5 relevant, cap 3
    assert _cursor(backend) == "5"
    run()
    assert set(state["starts"]) == {5}


def test_a_failed_cursor_write_does_not_fail_the_search(world, monkeypatch, caplog):
    state, run, backend = world
    state["relevant"] = {"https://example.org/p1"}

    def down(*a, **k):
        raise OSError("dynamo down")

    monkeypatch.setattr("src.utils.search_cache.cache_result", down)
    assert run() is True
    assert "OBSERV openserp.page_cursor_write_failed" in caplog.text


def test_every_decision_is_logged(world, caplog):
    import logging

    state, run, backend = world
    with caplog.at_level(logging.INFO):
        run()  # nothing relevant on the first page: unchanged, still said
    assert "OBSERV openserp.page entity=place#01HPLACE start=0 next=0" in caplog.text


def test_a_group_image_category_at_its_cap_does_not_hold_the_page():
    trace = oe._new_trace("people_groups", "f", {}, "x")
    with oe._tracing(trace):
        trace["paging"] = {"start": 5, "capped": False, "new": 0, "known": 0}
        oe._group_capped(False)
        assert trace["paging"]["capped"] is False
        oe._group_capped(True)
        assert trace["paging"]["capped"] is True
