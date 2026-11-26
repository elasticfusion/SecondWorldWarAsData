"""OpenSERP pages go to the human review queue, not into the entity's JSON (Group 3, owner 2026-11-17)."""

import logging
from unittest.mock import patch

import src.enrichment.openserp_enrichment as oe
from src.utils import find_queue

URL = "https://example.org/obit/john-doe"
PAGE = {
    "url": URL,
    "title": "John Doe obituary",
    "summary": "He served with the 3rd Armored.",
    "fetched_at": 1,
    "source": "openserp",
}


def _person_candidate():
    return {"web_results": [{"url": URL, "title": "John Doe", "description": "x"}]}


def _apply(data, **kw):
    with patch.object(oe, "process_positive_url", return_value=PAGE) as proc:
        changed = oe._apply_verified_web(
            _person_candidate(), data, "John Doe", object(), kw.get("max_web", 5)
        )
    return changed, proc


def test_a_person_page_is_queued_and_military_awards_is_not_written(queued_finds):
    data = {"PersonID": "01HPERSON", "name": "John Doe"}
    changed, _ = _apply(data)
    assert changed is True
    assert "military_awards" not in data and "web_results" not in data
    (e,) = queued_finds()
    assert e["review_status"] == "pending" and e["endpoint"] == URL
    p = e["request_params"]
    assert (p["origin_entity_type"], p["origin_entity_id"]) == ("person", "01HPERSON")
    assert p["summary"] == PAGE["summary"] and p["relevancy"] == find_queue.RELEVANT


def test_a_page_already_in_the_queue_costs_no_fetch_and_no_grok_request(queued_finds):
    data = {"PersonID": "01HPERSON", "name": "John Doe"}
    _apply(data)
    changed, proc = _apply(data)
    assert changed is False and proc.call_count == 0
    assert len(queued_finds()) == 1
    assert oe.get_metrics()["finds_already_queued"] >= 1


def test_a_rejected_find_is_never_asked_about_again(queued_finds):
    from src.enrichment import fetch_review

    data = {"PersonID": "01HPERSON", "name": "John Doe"}
    _apply(data)
    assert fetch_review.reject_fetch_find(queued_finds()[0])
    changed, proc = _apply(data)
    assert changed is False and proc.call_count == 0


def test_the_same_page_is_queued_once_per_entity():
    a = {"PersonID": "01HPERSONA", "name": "John Doe"}
    b = {"PersonID": "01HPERSONB", "name": "John Doe"}
    assert _apply(a)[0] is True and _apply(b)[0] is True


def test_max_web_caps_the_new_finds_per_entity(queued_finds):
    cand = {
        "web_results": [
            {"url": f"https://example.org/p{i}", "title": "John Doe"} for i in range(4)
        ]
    }

    def page(url, *a, **k):
        return {**PAGE, "url": url}

    with (
        patch.object(oe, "process_positive_url", side_effect=page),
        patch.object(oe, "_verify_results_batch") as batch,
    ):
        oe._apply_verified_web(cand, {"PersonID": "01H"}, "John Doe", object(), 2)
    assert (
        len(batch.call_args.args[1]) == 4
    )  # the four results asked about in one request
    assert len(queued_finds()) == 2


def test_a_page_grok_rejects_is_not_queued(queued_finds):
    with patch.object(oe, "process_positive_url", return_value=None):
        changed = oe._apply_verified_web(
            _person_candidate(), {"PersonID": "01H"}, "John Doe", object(), 5
        )
    assert changed is False and queued_finds() == []


def test_a_failed_queue_write_leaves_the_entity_unsearched(monkeypatch, tmp_path):
    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    data = {"PersonID": "01H", "name": "John Doe"}

    def search():
        with patch.object(oe, "process_positive_url", return_value=PAGE):
            return oe._apply_verified_web(
                _person_candidate(), data, "John Doe", object(), 5
            )

    with patch.object(oe, "_settle", side_effect=AssertionError("must not settle")):
        out = oe._search_entity("person", None, data, "John Doe", tmp_path, search)
    assert out is None  # not marked searched, not written: the next run asks again
    assert oe.get_metrics()["finds_failed"] >= 1


def test_find_page_raises_when_the_queue_write_fails(monkeypatch):
    import pytest

    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    with patch.object(oe, "process_positive_url", return_value=PAGE):
        with pytest.raises(oe.FindNotStored):
            oe._apply_verified_web(
                _person_candidate(), {"PersonID": "01H"}, "John Doe", object(), 5
            )


def test_the_group_id_may_be_either_field_name():
    assert (
        find_queue.entity_origin("people_group", {"GroupID": "01HG"}).entity_id
        == "01HG"
    )
    assert (
        find_queue.entity_origin("people_group", {"PeopleGroupID": "01HP"}).entity_id
        == "01HP"
    )


def test_an_entity_without_a_ulid_queues_nothing_and_says_so(caplog, queued_finds):
    with caplog.at_level(logging.ERROR):
        assert find_queue.entity_origin("place", {"current_name": "Metz"}) is None
    assert "OBSERV find_queue.entity_without_id" in caplog.text
    with patch.object(oe, "process_positive_url", return_value=PAGE) as proc:
        changed = oe._apply_verified_web(
            _person_candidate(), {"name": "John Doe"}, "John Doe", object(), 5
        )
    assert changed is False and proc.call_count == 0 and queued_finds() == []
    assert oe.get_metrics()["finds_no_origin"] >= 1


def test_failures_show_in_the_summary_and_raise_an_anomaly(caplog):
    oe.reset_metrics()
    oe._metric("finds_failed", 2)
    snap = oe.get_metrics()
    with caplog.at_level(logging.INFO):
        oe._emit_openserp_summary(snap)
        oe._emit_openserp_anomalies(snap)
    assert "finds_failed=2" in caplog.text
    assert "OBSERV openserp.anomaly finds_failed=2" in caplog.text


def test_grok_suggested_biography_urls_are_queued_not_written(
    queued_finds, monkeypatch
):
    from src.extraction import enrich_biographies as eb

    person = {"PersonID": "01HPERSON", "name": "John Doe"}  # the ULID is on the person
    profile = {"source_urls": ["https://example.org/bio", "https://example.org/other"]}
    monkeypatch.setattr(
        eb, "_fetch_url_content", lambda u, **k: "John Doe, 3rd Armored"
    )
    monkeypatch.setattr(eb, "_validate_url_relevance", lambda u, t, n, g: "bio" in u)
    assert eb._queue_suggested_urls(profile, person, "John Doe", object()) == 1
    assert "biography_sources" not in profile and "source_urls" not in profile
    (e,) = queued_finds("llm-find")
    assert e["endpoint"] == "https://example.org/bio"
    assert e["request_params"]["origin_entity_id"] == "01HPERSON"
    # a second run asks nothing and fetches nothing
    again = {"source_urls": ["https://example.org/bio"]}
    monkeypatch.setattr(eb, "_fetch_url_content", lambda u, **k: 1 / 0)
    assert eb._queue_suggested_urls(again, person, "John Doe", object()) == 0
    assert len(queued_finds("llm-find")) == 1


def test_two_people_may_both_cite_the_same_page(queued_finds, monkeypatch):
    from src.extraction import enrich_biographies as eb

    monkeypatch.setattr(eb, "_fetch_url_content", lambda u, **k: "text")
    monkeypatch.setattr(eb, "_validate_url_relevance", lambda *a: True)
    for pid in ("01HA", "01HB"):
        prof = {"source_urls": ["https://example.org/shared"]}
        assert eb._queue_suggested_urls(prof, {"PersonID": pid}, "J Doe", object()) == 1
    assert len(queued_finds("llm-find")) == 2


def test_a_suggested_url_that_could_not_be_stored_leaves_the_person_unstamped(
    monkeypatch, tmp_path
):
    """The failed URL is not parked in the profile (the people schema forbids the key): the whole
    step raises, the caller does not stamp the person, and the next run asks again."""
    import json

    from src.extraction import enrich_biographies as eb

    monkeypatch.setattr(eb, "_fetch_url_content", lambda u, **k: "text")
    monkeypatch.setattr(eb, "_validate_url_relevance", lambda *a: True)
    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    prof = {"source_urls": ["https://example.org/bio", 7]}  # a non-string is ignored
    import pytest

    with pytest.raises(find_queue.FindNotStored):
        eb._queue_suggested_urls(prof, {"PersonID": "01H"}, "J Doe", object())
    assert "source_urls" not in prof

    f = tmp_path / "p.json"
    f.write_text(
        json.dumps({"PersonID": "01H", "name": "J Doe", "biographical_profile": {}})
    )
    monkeypatch.setattr(
        eb,
        "_run_enrichment_steps",
        lambda *a, **k: (_ for _ in ()).throw(
            find_queue.FindNotStored("https://example.org/bio")
        ),
    )
    assert eb.enrich_person_biography(f, object()) is False
    assert "enrichment_status" not in json.loads(f.read_text())  # not stamped


def test_a_suggested_url_list_of_the_wrong_shape_is_ignored(queued_finds):
    from src.extraction import enrich_biographies as eb

    for bad in ("https://example.org/a", {"a": 1}, None):
        prof = {"source_urls": bad}
        assert eb._queue_suggested_urls(prof, {"PersonID": "01H"}, "J", object()) == 0
    assert queued_finds("llm-find") == []


def test_an_unsafe_url_costs_no_fetch_and_no_grok_request(queued_finds):
    with patch.object(oe, "process_positive_url") as proc:
        changed = oe._apply_verified_web(
            {"web_results": [{"url": "http://127.0.0.1/x", "title": "John Doe"}]},
            {"PersonID": "01H"},
            "John Doe",
            object(),
            5,
        )
    assert changed is False and proc.call_count == 0
    assert oe.get_metrics()["finds_refused"] >= 1


def test_a_suggested_url_with_a_line_break_is_neither_logged_nor_fetched(
    queued_finds, monkeypatch, caplog
):
    from src.extraction import enrich_biographies as eb

    monkeypatch.setattr(eb, "_fetch_url_content", lambda u, **k: 1 / 0)
    prof = {"source_urls": ["https://example.org/a\nOBSERV fake status=error"]}
    with caplog.at_level(logging.INFO):
        assert eb._queue_suggested_urls(prof, {"PersonID": "01H"}, "J", object()) == 0
    assert "OBSERV fake" not in caplog.text and queued_finds("llm-find") == []


def test_a_record_without_a_ulid_is_not_searched_and_not_marked(
    tmp_path, caplog, queued_finds
):
    calls = []
    with caplog.at_level(logging.ERROR):
        out = oe._search_entity(
            "people", None, {"name": "John Doe"}, "John Doe", tmp_path,
            lambda: calls.append(1) or True,
        )  # fmt: skip
    assert out is None and calls == []  # nothing searched; the caller marks nothing
    assert "OBSERV find_queue.entity_without_id" in caplog.text
    assert "bypassed it" in caplog.text  # says where to look
    assert queued_finds() == []


def test_equipment_needs_no_ulid_to_be_searched(tmp_path):
    assert oe.lacks_identity("equipment", {"common_name": "M4"}) is False


def test_the_worker_skips_a_record_without_a_ulid_and_marks_nothing(monkeypatch):
    import openserp_worker as w

    monkeypatch.setattr(w, "_safe_entity_path", lambda *a: True)

    class Storage:
        def read_json(self, path):
            return {"name": "x"}

        def write_json(self, *a):
            raise AssertionError("an unsearched entity must not be written")

    monkeypatch.setattr(
        oe, "enrich_one_person", lambda *a: (_ for _ in ()).throw(AssertionError("no"))
    )
    out = w.process_message(
        {"entity_type": "people", "entity_path": "people/x.json"},
        Storage(),
        "http://serp",
        object(),
    )
    assert out == "skip"


def test_every_searched_kind_needs_a_ulid():
    ok = "01HX7YZABCDEFGHJKMNPQRSTVW"
    cases = {
        "people": "PersonID",
        "people_groups": "GroupID",
        "places": "PlaceID",
        "source_section": "SourceSectionID",
    }
    for entity, field in cases.items():
        assert oe.lacks_identity(entity, {}) is True, entity
        assert oe.lacks_identity(entity, {field: ok}) is False, entity
        for bad in ("", None, 0):
            assert oe.lacks_identity(entity, {field: bad}) is True, (entity, bad)
    # the people_groups schema says GroupID; some code writes PeopleGroupID: both are read
    assert oe.lacks_identity("people_groups", {"PeopleGroupID": ok}) is False


def test_a_person_with_awards_is_still_web_searched(monkeypatch):
    """military_awards (filled only by an award extraction) no longer gates the web search."""
    sent = []
    monkeypatch.setattr(
        oe,
        "_search_openserp",
        lambda q, u, **k: sent.append(q) or [{"url": URL, "title": "John Doe"}],
    )
    data = {
        "PersonID": "01HPERSON",
        "name": "John Doe",
        "military_awards": [{"award": "Bronze Star"}],
    }
    c = oe._collect_person_candidates(None, "John Doe", data, "http://serp")
    assert sent and c["web_results"]
    assert {h["url"] for h in c["web_results"]} == {URL}
