"""Re-check of approved finds by checksum (owner 2026-11-17 / 2026-11-24): unchanged -> recorded
(no Grok), changed -> a NEW find queued for review (the approved one and its page stay), dead ->
recorded with the kept page intact, transient -> retried soon."""

import logging
import time

import pytest

from src.enrichment import find_recheck as fr
from src.utils import fetch_provenance as fp
from src.utils import find_pages, find_queue, url_fetch

URL = "https://example.org/unit-history"
PAGE = "<html><head><title>3rd Armored</title></head><body><p>The 3rd Armored at Mons.</p></body></html>"
DAY = 86400
NOW = 2_000_000_000.0


def _approved(
    status=fp.SUBMITTED, fetched_days_ago=200, html=PAGE, provider="openserp-find"
):
    page = find_pages.keep(URL, html) if html else {"page_kept": False}
    origin = find_queue.Origin(entity_type="person", entity_id="01HPERSON", query="q")
    assert (
        find_queue.enqueue_find(
            provider=provider,
            url=URL,
            verdict=find_queue.RELEVANT,
            origin=origin,
            summary="OpenSERP page for person: 3rd Armored",
            params={"title": "3rd Armored", "summary": "He served at Mons.", **page},
        )
        == find_queue.ENQUEUED
    )
    from src.enrichment import fetch_review

    (event,) = [
        e for e in fetch_review._load_events_counted()[0] if e["provider"] == provider
    ]
    event["review_status"] = status
    event["request_params"]["page_fetched_at"] = NOW - fetched_days_ago * DAY
    return event


@pytest.fixture
def web(monkeypatch):
    """url_fetch.fetch answers from ``answers`` (a FetchResult per call); registry calls recorded."""
    answers, calls = [], {"fetch": 0, "finalize": 0, "release": 0}

    def fetch(url, **k):
        calls["fetch"] += 1
        return answers.pop(0)

    monkeypatch.setattr(url_fetch, "fetch", fetch)
    monkeypatch.setattr(
        "src.utils.url_registry.finalize_searched",
        lambda *a, **k: calls.__setitem__("finalize", calls["finalize"] + 1),
    )
    monkeypatch.setattr(
        "src.utils.url_registry.release",
        lambda *a, **k: calls.__setitem__("release", calls["release"] + 1),
    )
    return answers, calls


def _live(html):
    return url_fetch.FetchResult(
        url=URL, source=url_fetch.LIVE, content=html, http_status=200
    )


def _run_on(monkeypatch, events, config=None):
    monkeypatch.setattr("src.enrichment.fetch_review.load_events", lambda: (events, 0))
    return fr.run(config, now=NOW)


def test_an_unchanged_page_is_recorded_without_a_new_find(
    monkeypatch, web, queued_finds
):
    answers, calls = web
    event = _approved()
    answers.append(_live(PAGE.replace("<p>", "<p class='x'>  ")))  # layout only
    assert _run_on(monkeypatch, [event]) == 1
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.UNCHANGED and state["next_check_at"] == NOW + 180 * DAY
    assert len(queued_finds()) == 1 and calls["finalize"] == 1


def test_a_script_or_ad_change_is_not_a_change(monkeypatch, web):
    answers, _ = web
    event = _approved()
    answers.append(_live(PAGE.replace("</body>", "<script>track(42)</script></body>")))
    _run_on(monkeypatch, [event])
    assert fr.state_for(event["fetch_event_id"])["status"] == fr.UNCHANGED


def test_a_changed_page_is_queued_as_a_new_find_and_the_approved_one_stays(
    monkeypatch, web, queued_finds
):
    answers, _ = web
    event = _approved()
    new_html = PAGE.replace("at Mons", "at Mons and Aachen")
    answers.append(_live(new_html))
    assert _run_on(monkeypatch, [event]) == 1
    old, new = queued_finds()
    assert old["fetch_event_id"] == event["fetch_event_id"]
    p = new["request_params"]
    assert new["review_status"] == fp.PENDING and new["endpoint"] == URL
    assert p["recheck_of"] == event["fetch_event_id"]
    assert p["previous_page_sha256"] == event["request_params"]["page_sha256"]
    assert p["page_sha256"] != p["previous_page_sha256"]
    assert p["previous_summary"] == "He served at Mons."
    assert (p["origin_entity_type"], p["origin_entity_id"]) == ("person", "01HPERSON")
    assert b"Aachen" in find_pages.load(
        p
    )  # the new page is kept, under its own checksum
    assert find_pages.load(event["request_params"]) != find_pages.load(
        p
    )  # the old one too
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.CHANGED and state["next_check_at"] is None
    assert state["history"] == []  # first check: nothing before it
    # the changed find is not checked again (its new version is a find of its own)
    assert fr.due_finds([event], NOW + 1000 * DAY, 180) == []


def test_the_same_change_seen_twice_is_queued_once(monkeypatch, web, queued_finds):
    answers, _ = web
    event = _approved()
    new_html = PAGE.replace("Mons", "Liege")
    answers.extend([_live(new_html), _live(new_html)])
    monkeypatch.setattr(fr, "_save", lambda *a: True)  # as if the record were lost
    _run_on(monkeypatch, [event])
    _run_on(monkeypatch, [event])
    assert len(queued_finds()) == 2  # the approved find + ONE revision


def test_a_dead_page_keeps_its_kept_copy(monkeypatch, web):
    answers, calls = web
    event = _approved()
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.DEAD, detail="gone"))
    _run_on(monkeypatch, [event])
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.DEAD and state["dead_since"]
    assert state["next_check_at"] == NOW + 180 * DAY
    assert find_pages.load(event["request_params"])  # still there for the citation
    assert calls["finalize"] == 0


def test_only_the_archive_having_it_counts_as_dead(monkeypatch, web):
    answers, calls = web
    event = _approved()
    answers.append(
        url_fetch.FetchResult(
            url=URL,
            source=url_fetch.WAYBACK,
            content=PAGE,
            archived_url="https://web.archive.org/web/2020/" + URL,
            detail="wayback (live 404/410)",
        )
    )
    _run_on(monkeypatch, [event])
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.DEAD and state["archived_url"].startswith(
        "https://web"
    )
    assert calls["finalize"] == 1  # the claim is settled


def test_a_page_that_comes_back_is_compared_again(monkeypatch, web):
    answers, _ = web
    event = _approved()
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.DEAD))
    _run_on(monkeypatch, [event])
    answers.append(_live(PAGE))
    monkeypatch.setattr("src.enrichment.fetch_review.load_events", lambda: ([event], 0))
    fr.run(None, now=NOW + 181 * DAY)
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.UNCHANGED
    assert [h["status"] for h in state["history"]] == [fr.DEAD]


def test_a_transient_failure_is_retried_soon_and_releases_its_claim(monkeypatch, web):
    answers, calls = web
    event = _approved()
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.RATE_LIMITED))
    _run_on(monkeypatch, [event])
    state = fr.state_for(event["fetch_event_id"])
    assert state["next_check_at"] == NOW + fr.RETRY_DAYS * DAY
    assert calls["release"] == 1


def test_a_find_with_no_kept_page_gets_a_baseline(monkeypatch, web, queued_finds):
    answers, _ = web
    event = _approved(html="")
    answers.append(_live(PAGE))
    _run_on(monkeypatch, [event])
    state = fr.state_for(event["fetch_event_id"])
    assert state["status"] == fr.BASELINE and state["text_sha256"].startswith("sha256:")
    assert len(queued_finds()) == 1


def test_only_approved_finds_that_are_due_are_checked(monkeypatch, web):
    answers, calls = web
    pending = _approved(status=fp.PENDING)
    assert _run_on(monkeypatch, [pending]) == 0
    fresh = _approved(status=fp.AFFIRMED, fetched_days_ago=10, provider="llm-find")
    assert _run_on(monkeypatch, [fresh]) == 0
    rejected = _approved(status=fp.REJECTED, provider="map-find")
    assert _run_on(monkeypatch, [rejected]) == 0
    assert calls["fetch"] == 0


def test_the_cap_checks_the_oldest_first(monkeypatch, web):
    answers, calls = web
    events = []
    for n, days in enumerate((300, 900, 500)):
        e = _approved(provider=sorted(fp.FIND_PROVIDERS)[n], fetched_days_ago=days)
        events.append(e)
    answers.extend([_live(PAGE)] * 2)
    checked = _run_on(monkeypatch, events, {"find_recheck": {"max_per_run": 2}})
    assert checked == 2 and calls["fetch"] == 2
    assert (
        fr.state_for(events[0]["fetch_event_id"]) is None
    )  # 300 days: the youngest waits


def test_a_change_that_cannot_be_queued_is_tried_again(monkeypatch, web, caplog):
    answers, _ = web
    event = _approved()
    answers.append(_live(PAGE.replace("Mons", "Metz")))
    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    with caplog.at_level(logging.WARNING):
        assert _run_on(monkeypatch, [event]) == 0
    state = fr.state_for(event["fetch_event_id"])  # tried again after a short backoff
    assert state["next_check_at"] == NOW + fr.FAILED_BACKOFF_DAYS * DAY
    assert state["last_attempt"] == "change not kept or queued"
    assert "OBSERV find_recheck.summary" in caplog.text and "failed=1" in caplog.text


def test_one_broken_find_does_not_stop_the_run(monkeypatch, web, caplog):
    answers, _ = web
    a, b = _approved(), _approved(provider="llm-find")
    answers.extend([RuntimeError, _live(PAGE)])

    real = url_fetch.fetch

    def fetch(url, **k):
        got = real(url, **k)
        if got is RuntimeError:
            raise RuntimeError("boom")
        return got

    monkeypatch.setattr(url_fetch, "fetch", fetch)
    with caplog.at_level(logging.ERROR):
        assert _run_on(monkeypatch, [a, b]) == 1
    assert "OBSERV find_recheck.error" in caplog.text


def test_unreadable_events_are_said(monkeypatch, caplog):
    monkeypatch.setattr("src.enrichment.fetch_review.load_events", lambda: ([], 3))
    with caplog.at_level(logging.WARNING):
        assert fr.run(None, now=NOW) == 0
    assert "OBSERV find_recheck.load_errors errors=3" in caplog.text


def test_the_text_checksum_ignores_markup_and_whitespace():
    a = find_pages.text_sha256("<p>The   3rd&nbsp;Armored</p>")
    b = find_pages.text_sha256("<div><b>The 3rd</b>\n Armored</div>")
    assert a == b
    assert a != find_pages.text_sha256("<p>The 4th Armored</p>")


def test_settings_come_from_config_and_bad_values_fall_back():
    assert fr._settings(None) == (180, 50)
    assert fr._settings({"find_recheck": {"interval_days": 30, "max_per_run": 5}}) == (
        30,
        5,
    )
    assert fr._settings({"find_recheck": {"interval_days": 0, "max_per_run": "x"}}) == (
        180,
        50,
    )


def test_phase3_runs_the_recheck():
    from pathlib import Path

    assert '_run_step("find_recheck"' in Path("phase3_enrich_data.py").read_text()


def test_a_revision_is_its_own_queue_item_and_a_repeat_is_not():
    origin = find_queue.Origin(entity_type="place", entity_id="01HPLACE")
    kw = dict(provider="openserp-find", url=URL, verdict=find_queue.RELEVANT, origin=origin,
              summary="s")  # fmt: skip
    assert find_queue.enqueue_find(**kw) == find_queue.ENQUEUED
    assert find_queue.enqueue_find(**kw) == find_queue.ALREADY_QUEUED
    assert find_queue.enqueue_find(**kw, revision="sha256:a") == find_queue.ENQUEUED
    assert (
        find_queue.enqueue_find(**kw, revision="sha256:a") == find_queue.ALREADY_QUEUED
    )


def test_time_is_not_needed_for_due(monkeypatch):
    """A find with no fetch time falls back to when it was queued."""
    e = {"fetch_event_id": "x", "provider": "openserp-find", "endpoint": URL,
         "review_status": fp.AFFIRMED, "queried_at": "2020-01-01T00:00:00+00:00",
         "request_params": {}}  # fmt: skip
    assert fr.due_finds([e], time.time(), 180) == [(e, None)]


def test_a_changed_page_does_not_move_the_urls_pointer(monkeypatch, web):
    answers, _ = web
    event = _approved()
    approved_sha = event["request_params"]["page_sha256"]
    answers.append(_live(PAGE.replace("Mons", "Bastogne")))
    _run_on(monkeypatch, [event])
    assert (
        find_pages.lookup(URL)["page_sha256"] == approved_sha
    )  # the cached summary's page


def test_a_retry_keeps_the_date_it_was_first_seen_dead(monkeypatch, web):
    answers, _ = web
    event = _approved()
    monkeypatch.setattr("src.enrichment.fetch_review.load_events", lambda: ([event], 0))
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.DEAD))
    fr.run(None, now=NOW)
    first = fr.state_for(event["fetch_event_id"])["dead_since"]
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.RATE_LIMITED))
    fr.run(None, now=NOW + 181 * DAY)
    mid = fr.state_for(event["fetch_event_id"])
    assert mid["status"] == fr.DEAD and mid["dead_since"] == first
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.DEAD))
    fr.run(None, now=NOW + 200 * DAY)
    last = fr.state_for(event["fetch_event_id"])
    assert last["dead_since"] == first
    assert [h["status"] for h in last["history"]] == [
        fr.DEAD
    ]  # the retry is not a state


def test_a_url_the_registry_knows_is_dead_is_dead(monkeypatch, web):
    from src.utils.url_registry import UrlRecord

    answers, _ = web
    event = _approved()
    rec = UrlRecord(
        url=URL, status="dead", relevant=False, context="", source="", examined_at=0
    )
    answers.append(
        url_fetch.FetchResult(url=URL, source=url_fetch.SKIP, registry_record=rec)
    )
    _run_on(monkeypatch, [event])
    assert fr.state_for(event["fetch_event_id"])["status"] == fr.DEAD


def test_a_page_too_complex_to_compare_is_said(monkeypatch, web, caplog):
    from src.utils.html_safety import PageTooComplex

    answers, _ = web
    event = _approved()
    answers.append(_live(PAGE))

    def boom(html):
        raise PageTooComplex("too big")

    monkeypatch.setattr(find_pages, "text_sha256", boom)
    with caplog.at_level(logging.WARNING):
        _run_on(monkeypatch, [event])
    assert "OBSERV find_recheck.page_too_complex" in caplog.text
    assert fr.state_for(event["fetch_event_id"])["next_check_at"] == NOW + 180 * DAY


def test_the_history_is_capped(monkeypatch, web):
    answers, _ = web
    event = _approved()
    monkeypatch.setattr("src.enrichment.fetch_review.load_events", lambda: ([event], 0))
    for n in range(14):
        answers.append(_live(PAGE))
        fr.run(None, now=NOW + n * 181 * DAY)
    assert len(fr.state_for(event["fetch_event_id"])["history"]) == fr._HISTORY_MAX


def test_a_bad_setting_names_its_own_section(caplog):
    from src.utils import observe

    observe._counts.clear()
    with caplog.at_level(logging.WARNING):
        fr._settings({"find_recheck": {"max_per_run": 0}})
    assert "find_recheck.max_per_run" in caplog.text


def test_a_url_searched_lately_by_another_step_waits_the_interval(monkeypatch, web):
    answers, _ = web
    event = _approved()
    answers.append(url_fetch.FetchResult(url=URL, source=url_fetch.SKIP))
    _run_on(monkeypatch, [event])
    assert fr.state_for(event["fetch_event_id"])["next_check_at"] == NOW + 180 * DAY


def test_a_find_that_raised_backs_off(monkeypatch, web):
    answers, _ = web
    event = _approved()
    monkeypatch.setattr(url_fetch, "fetch", lambda *a, **k: 1 / 0)
    assert _run_on(monkeypatch, [event]) == 0
    state = fr.state_for(event["fetch_event_id"])
    assert state["next_check_at"] == NOW + fr.FAILED_BACKOFF_DAYS * DAY
    assert state["last_attempt"] == "error: ZeroDivisionError"
