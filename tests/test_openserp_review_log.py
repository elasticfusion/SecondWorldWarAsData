"""Task 1.2d (owner 2026-11-18): what an OpenSERP search that finds nothing does.

Owner rules: OpenSERP found nothing -> log it for later review, mark searched, move on; Grok
rejected every candidate -> log it WITH the Grok prompt (it may need revising), mark searched,
move on; Grok failed -> the shared client (task 1.2e). OpenSERP itself unavailable is not a
search: the entity is left unmarked for the next run. The start-of-run reset that cleared
"searched" on every entity with no results is gone, and people are searched and written one at a
time, so a run killed midway does not repeat the searches already done.
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import src.enrichment.openserp_enrichment as oe  # noqa: E402


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "Utah")
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"openserp": {"rate_limit_seconds": 0}},
    )
    oe.reset_metrics()
    out = tmp_path / "output"
    (out / "places").mkdir(parents=True)
    (out / "people").mkdir()
    return out


def _place(out: Path, name: str, pid: str) -> Path:
    f = out / "places" / f"{pid}.json"
    f.write_text(json.dumps({"name": name, "PlaceID": pid}))
    return f


def _review(out: Path):
    files = list((out / "review" / "openserp_misses" / "Utah").glob("*-t1.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines()]


def _searching(monkeypatch, outcome):
    """enrich_one_place stand-in that runs a query with ``outcome`` and optional verdicts."""

    def run(data, url, grok):
        status, verdicts = outcome
        oe._trace_query(f"{data['name']} WWII", status, 3 if status == "results" else 0)
        for v in verdicts:
            oe._trace_verdict(
                "A title", "http://a", v, "PROMPT TEXT", "grok-4.3", False
            )
        return False

    monkeypatch.setattr(oe, "enrich_one_place", run)


@pytest.mark.parametrize(
    "outcome,logged",
    [(("empty", []), "empty"), (("results", []), "excluded"),
     (("results", ["NO", "NO"]), "rejected")],
)  # fmt: skip
def test_a_miss_is_logged_marked_searched_and_moved_past(
    corpus, monkeypatch, outcome, logged
):
    f = _place(corpus, "Bastogne", "P1")
    _searching(monkeypatch, outcome)
    oe.enrich_places_with_openserp(corpus / "places", "http://serp", object())
    assert json.loads(f.read_text())["openserp_searched"] is True
    [rec] = _review(corpus)
    assert rec["outcome"] == logged and rec["ids"] == {"PlaceID": "P1"}
    assert rec["queries"][0]["query"] == "Bastogne WWII"
    if logged == "rejected":  # the prompt is kept so it can be revised
        assert rec["verdicts"][0]["prompt"] == "PROMPT TEXT"
        assert rec["verdicts"][0]["model"] == "grok-4.3"
    else:
        assert "verdicts" not in rec


def test_openserp_unavailable_is_not_a_search(corpus, monkeypatch):
    f = _place(corpus, "Bastogne", "P1")
    _searching(monkeypatch, ("error", []))
    oe.enrich_places_with_openserp(corpus / "places", "http://serp", object())
    assert "openserp_searched" not in json.loads(f.read_text())
    assert _review(corpus) == []
    assert oe.get_metrics()["entities_unavailable"] == 1


def test_a_found_or_nothing_new_result_is_not_logged(corpus, monkeypatch):
    _place(corpus, "Bastogne", "P1")
    _searching(monkeypatch, ("results", ["YES"]))
    oe.enrich_places_with_openserp(corpus / "places", "http://serp", object())
    assert _review(corpus) == []


@pytest.mark.parametrize(
    "queries,verdicts,changed,want",
    [([], [], False, "empty"), (["empty", "error"], [], False, "unavailable"),
     (["results"], ["NO"], False, "rejected"), (["results"], ["YES"], True, "found"),
     (["error"], ["YES"], True, "found"),
     (["results", "error"], ["NO"], False, "rejected")],  # results decide (review)
)  # fmt: skip
def test_search_outcome(queries, verdicts, changed, want):
    trace = {"queries": [{"status": q} for q in queries],
             "verdicts": [{"verdict": v} for v in verdicts]}  # fmt: skip
    assert oe.search_outcome(trace, changed) == want


def test_search_records_each_query_status(monkeypatch):
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda *_: None)
    oe.reset_circuit()
    trace = oe._new_trace("places", "f", {}, "x")
    responses = iter(
        [mock.Mock(status_code=200, json=lambda: []),
         mock.Mock(status_code=503, json=lambda: {})]
    )  # fmt: skip
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch", lambda *a, **k: next(responses)
    )
    with oe._tracing(trace):
        oe._search_openserp("q1", "http://serp")
        oe._search_openserp("q2", "http://serp")
    assert [q["status"] for q in trace["queries"]] == ["empty", "error"]


def test_the_verifier_records_its_prompt_and_model(monkeypatch):
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda *a: None)
    monkeypatch.setattr("src.utils.search_cache.cache_result", lambda *a: None)
    grok = mock.MagicMock()
    grok.extract_json.return_value = {"relevant": "NO"}
    grok._get_model.return_value = "grok-4.3"
    trace = oe._new_trace("people", "f", {}, "x")
    with oe._tracing(trace):
        oe._verify_result("A title", "Photo of X WWII", grok, url="http://a")
    [v] = trace["verdicts"]
    assert (
        v["verdict"] == "NO"
        and "Photo of X WWII" in v["prompt"]
        and v["model"] == "grok-4.3"
    )


def test_people_are_searched_and_written_one_at_a_time(corpus, monkeypatch):
    """A run killed after the first person keeps that person's search (it is written before the
    second is searched): the re-run does not search them again."""
    for n in ("Able", "Baker"):
        (corpus / "people" / f"{n}.json").write_text(json.dumps({"name": n}))
    events = []
    monkeypatch.setattr(
        oe,
        "_collect_person_candidates",
        lambda f, name, data, url: events.append(("search", name)) or {"name": name},
    )
    monkeypatch.setattr(oe, "_verify_and_apply", lambda *a, **k: False)
    real_write = oe.write_json_with_lock

    def write(f, data, **k):
        events.append(("write", data["name"]))
        if data["name"] == "Able":
            return real_write(f, data, **k)
        raise KeyboardInterrupt  # the task is killed while saving the second person

    monkeypatch.setattr(oe, "write_json_with_lock", write)
    with pytest.raises(KeyboardInterrupt):
        oe.enrich_people_with_openserp(corpus / "people", "http://serp", object())
    assert events == [("search", "Able"), ("write", "Able"), ("search", "Baker"),
                      ("write", "Baker")]  # fmt: skip
    events.clear()
    monkeypatch.setattr(oe, "write_json_with_lock", real_write)
    oe.enrich_people_with_openserp(corpus / "people", "http://serp", object())
    assert ("search", "Able") not in events and ("search", "Baker") in events


def test_the_start_of_run_reset_is_gone():
    import ecs_entrypoint as ee

    assert not hasattr(ee, "_reset_openserp_searched")


def test_cached_batch_verdicts_count_in_the_outcome(monkeypatch):
    """Review finding: a resumed run is mostly cache hits - cached verdicts must be traced, or an
    all-cached NO looks 'excluded' (no prompt) and a cached YES is missed."""
    results = [{"url": "http://a", "title": "A", "description": "d"},
               {"url": "http://b", "title": "B", "description": "d"}]  # fmt: skip
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, k: "NO")
    trace = oe._new_trace("people", "f", {}, "x")
    with oe._tracing(trace):
        trace["queries"].append({"status": "results"})
        oe._verify_results_batch("ctx", results, mock.MagicMock())
    assert oe.search_outcome(trace, False) == "rejected"
    assert all(v["cached"] and "ctx" in v["prompt"] for v in trace["verdicts"])

    monkeypatch.setattr(
        "src.utils.search_cache.get_cached",
        lambda s, k: "YES" if k.endswith("http://a") else None,
    )
    monkeypatch.setattr("src.utils.search_cache.cache_result", lambda *a: None)
    grok = mock.MagicMock()
    grok.extract_json.return_value = {"verdicts": ["NO"]}
    trace = oe._new_trace("people", "f", {}, "x")
    with oe._tracing(trace):
        trace["queries"].append({"status": "results"})
        oe._verify_results_batch("ctx", results, grok)
    assert oe.search_outcome(trace, False) == "nothing_new"


def test_genuine_empty_results_do_not_open_the_breaker(monkeypatch):
    """Security review: obscure entities return nothing; five in a row must not open the breaker
    and leave every later entity unsearched."""
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda *_: None)
    oe.reset_circuit()
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch",
        lambda *a, **k: mock.Mock(status_code=200, json=lambda: []),
    )
    for n in range(8):
        oe._search_openserp(f"q{n}", "http://serp")
    assert not oe._breaker_is_open()


@pytest.mark.parametrize(
    "book,task,want_book",
    [("Utah", "t1", "Utah"), ("..", "../x", "_all"), ("a/b", "", "a_b")],
)
def test_the_review_path_cannot_leave_its_directory(
    monkeypatch, tmp_path, book, task, want_book
):
    monkeypatch.setenv("BOOK_NAME", book)
    monkeypatch.setenv("ECS_TASK_ID", task)
    path = oe._review_path(tmp_path)
    assert path.parent == tmp_path / "review" / "openserp_misses" / want_book
    assert ".." not in path.parts and "/" not in path.name
