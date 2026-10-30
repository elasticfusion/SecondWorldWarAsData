"""Bounded-parallel Phase-2 bio enrichment (HIGH throughput fix).

Phase-2 bio enrichment was serial at ~3 min/person (fixed network timeouts), so
~60 people ≈ 3h. The per-person fetch (``enrich_person_from_sources``) does no
file I/O, so it is parallelized across a small bounded thread pool while PERSIST
stays serial/locked, and the Wikipedia courtesy delay is a shared thread-safe
rate limiter so politeness holds across threads.

All network is mocked — these tests make NO real calls.
"""

import logging
import threading
import time

import pytest

import src.extraction.bio_enrich_parallel as BP
import src.extraction.people as P


class _FakeGrok:
    """Minimal GrokClient stand-in: only ``batch_mode`` matters to the gate."""

    def __init__(self, batch_mode=False):
        self.batch_mode = batch_mode


def _people(n):
    return [{"name": f"Person {i}"} for i in range(n)]


def _counts():
    return P._enrich_counts.get(None)


# --------------------------------------------------------------------------- #
# Gating: only the real-time sync path parallelizes.
# --------------------------------------------------------------------------- #


def test_should_parallelize_happy_path():
    cfg = {
        "concurrency": {"max_enrichment_workers": 4, "bio_enrichment_parallel": True}
    }
    assert BP.should_parallelize(_people(3), _FakeGrok(), cfg) is True


def test_should_not_parallelize_in_batch_mode():
    cfg = {"concurrency": {"max_enrichment_workers": 4}}
    assert BP.should_parallelize(_people(3), _FakeGrok(batch_mode=True), cfg) is False


def test_should_not_parallelize_single_person():
    cfg = {"concurrency": {"max_enrichment_workers": 4}}
    assert BP.should_parallelize(_people(1), _FakeGrok(), cfg) is False


def test_should_not_parallelize_when_disabled():
    cfg = {
        "concurrency": {"bio_enrichment_parallel": False, "max_enrichment_workers": 4}
    }
    assert BP.should_parallelize(_people(3), _FakeGrok(), cfg) is False


def test_should_not_parallelize_single_worker():
    cfg = {"concurrency": {"max_enrichment_workers": 1}}
    assert BP.should_parallelize(_people(3), _FakeGrok(), cfg) is False


def test_worker_count_defaults_and_caps():
    assert BP._resolve_worker_count(None) == BP._DEFAULT_WORKERS
    assert BP._resolve_worker_count(
        {"concurrency": {"max_enrichment_workers": 9999}}
    ) == (BP._MAX_WORKERS_CAP)
    assert BP._resolve_worker_count(
        {"concurrency": {"max_enrichment_workers": "bad"}}
    ) == (BP._DEFAULT_WORKERS)
    assert BP._resolve_worker_count({"concurrency": {"max_enrichment_workers": 0}}) == 1


# --------------------------------------------------------------------------- #
# (a) N people enrich via the pool; the fetch fn is called exactly N times.
# --------------------------------------------------------------------------- #


def test_pool_calls_fetch_once_per_person():
    people = _people(6)
    calls = []
    lock = threading.Lock()

    def fetch(person, client):
        with lock:
            calls.append(person["name"])
        person["biographical_profile"] = {"birth_date": "1900"}
        return True

    bumps = []
    BP.prefetch_enrichment(
        people,
        _FakeGrok(),
        {"concurrency": {"max_enrichment_workers": 4}},
        fetch,
        lambda k: bumps.append(k),
        lambda person, enriched: None,
    )

    assert len(calls) == 6
    assert sorted(calls) == sorted(p["name"] for p in people)
    # every person got its own bio populated in place (no file I/O)
    assert all(p["biographical_profile"]["birth_date"] == "1900" for p in people)
    # counters: one "total" + one "enriched" per person
    assert bumps.count("total") == 6
    assert bumps.count("enriched") == 6


# --------------------------------------------------------------------------- #
# (b) A failing person does NOT abort the others (fail-safe).
# --------------------------------------------------------------------------- #


def test_one_failure_does_not_abort_batch():
    people = _people(5)

    def fetch(person, client):
        if person["name"] == "Person 2":
            raise RuntimeError("network boom")
        person["biographical_profile"] = {"birth_date": "1901"}
        return True

    bumps = []
    BP.prefetch_enrichment(
        people,
        _FakeGrok(),
        {"concurrency": {"max_enrichment_workers": 4}},
        fetch,
        lambda k: bumps.append(k),
        lambda person, enriched: None,
    )

    # 4 of 5 succeeded; the 1 failure is counted as an error, not a crash.
    enriched = [p for p in people if p.get("biographical_profile")]
    assert len(enriched) == 4
    assert bumps.count("total") == 5
    assert bumps.count("enriched") == 4
    assert bumps.count("errors") == 1


def test_fetch_one_captures_error_without_raising():
    person = {"name": "X"}

    def boom(p, c):
        raise ValueError("nope")

    returned, enriched_now, err = BP._fetch_one(person, _FakeGrok(), boom)
    assert returned is person
    assert enriched_now is False
    assert isinstance(err, ValueError)


# --------------------------------------------------------------------------- #
# (c) Persists still happen (serial, locked) for all successes.
#
# End-to-end through extract_people: parallel pre-fetch enriches, then the serial
# persist writes every person file + updates the index. Network + Grok mocked.
# --------------------------------------------------------------------------- #


def test_persists_serially_for_all_successes(tmp_path, monkeypatch):
    out = tmp_path / "out"
    (out / "people").mkdir(parents=True)

    # Event file with 2 sub-events, 3 people total.
    event = {
        "Chapter": "Ch1",
        "Event": {
            "EventID": "E1",
            "Sub-events": [
                {"Sub-eventID": "S1"},
                {"Sub-eventID": "S2"},
            ],
        },
    }
    import json

    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))

    # Extraction: S1 -> 2 people, S2 -> 1 person.
    batches = {
        "S1": [{"name": "Alpha"}, {"name": "Bravo"}],
        "S2": [{"name": "Charlie"}],
    }

    def fake_extract(sub_event, *a, **k):
        return batches.get(sub_event.get("Sub-eventID"), [])

    monkeypatch.setattr(P, "_extract_people_for_sub_event", fake_extract)
    monkeypatch.setattr(P, "_check_if_processed", lambda *a, **k: False)
    monkeypatch.setattr(P, "_load_book_metadata", lambda *a, **k: ("b", "a", "s"))
    monkeypatch.setattr(P, "_record_recall_gaps_safe", lambda *a, **k: None)
    monkeypatch.setattr(P, "_mark_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(P, "observe_yield", lambda *a, **k: None)
    monkeypatch.setattr(P, "warn_all_subevents_empty", lambda *a, **k: None)
    monkeypatch.setattr(P, "_mint_person_ids", lambda person, pid: None)

    # Force the parallel path on.
    monkeypatch.setattr(
        P,
        "load_config",
        lambda: {
            "concurrency": {
                "max_enrichment_workers": 4,
                "bio_enrichment_parallel": True,
            }
        },
        raising=False,
    )
    import src.utils.config as cfgmod

    monkeypatch.setattr(
        cfgmod,
        "load_config",
        lambda *a, **k: {
            "concurrency": {
                "max_enrichment_workers": 4,
                "bio_enrichment_parallel": True,
            }
        },
    )

    # The pure network fetch: enrich in place, no file I/O. Record the thread it ran on.
    fetch_threads = []

    def fake_fetch(person, client):
        fetch_threads.append(threading.current_thread().name)
        person["biographical_profile"] = {"birth_date": "1899"}
        person["enrichment_status"] = "enriched"
        return True

    import src.extraction.enrich_biographies as EB

    monkeypatch.setattr(EB, "enrich_person_from_sources", fake_fetch)

    # Capture serial persists; assert they run on the MAIN thread (serial/locked).
    persisted = []

    def fake_save(person_file, person):
        persisted.append((threading.current_thread().name, person["name"]))

    monkeypatch.setattr(P, "_save_person_file", fake_save)

    result = P.extract_people(event_file, _FakeGrok(), out)
    assert result == out / "people"

    # All 3 persisted, each with the pre-fetched bio.
    assert sorted(n for _, n in persisted) == ["Alpha", "Bravo", "Charlie"]
    # Persist happened on the MAIN thread (serial), fetch on worker threads (parallel).
    assert all(t == threading.main_thread().name for t, _ in persisted)
    assert any("bio-enrich" in t for t in fetch_threads)

    # Counters came out of the PARALLEL pass (counted once each, no double count).
    c = _counts()
    assert c["total"] == 3
    assert c["enriched"] == 3
    assert c["skipped"] == 0
    assert c["errors"] == 0


def test_prefetched_persist_does_not_refetch(monkeypatch):
    """When prefetched=True the serial _enrich_person_phase2 must NOT fetch or count."""
    P._reset_enrich_counts()
    import src.extraction.enrich_biographies as EB

    called = []
    monkeypatch.setattr(
        EB, "enrich_person_from_sources", lambda *a, **k: called.append(1) or True
    )
    P._enrich_person_phase2({"name": "Z"}, _FakeGrok(), prefetched=True)
    assert called == []  # no fetch
    assert _counts()["total"] == 0  # no double-count


# --------------------------------------------------------------------------- #
# (d) The Wikipedia rate-limiter bounds the request rate across threads.
# --------------------------------------------------------------------------- #


def test_wikipedia_rate_limit_bounds_concurrent_rate(monkeypatch):
    import src.extraction.enrich_biographies as EB

    # Reset the shared limiter state and shrink the window so the test is fast.
    sw = EB.search_wikipedia
    for attr in ("_lock", "_last_call"):
        if hasattr(sw, attr):
            delattr(sw, attr)

    real_sleep = time.sleep
    slept = []
    orig_time = time.time
    fake_now = {"t": 1000.0}

    monkeypatch.setattr(EB, "search_wikipedia", sw)  # keep same object
    # Patch time inside the limiter: advance a virtual clock so we can assert the
    # enforced spacing without actually waiting 5s per call.

    def fake_sleep(secs):
        slept.append(secs)
        fake_now["t"] += secs  # the limiter "waited", so virtual time advances

    def fake_time():
        return fake_now["t"]

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(time, "time", fake_time)

    # Fire the shared limiter from several threads at once.
    def hit():
        EB._wikipedia_rate_limit()
        # each successful "request" nudges virtual time a hair so lock ordering is stable
        fake_now["t"] += 0.001

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    real_sleep(0)  # noop; keep import used
    # 4 calls through a 1-request-per-5s limiter => at least 3 enforced waits,
    # each ~5s (minus tiny virtual drift). Proves politeness holds across threads
    # (not a naive per-call sleep that would let all 4 fire immediately).
    enforced = [s for s in slept if s > 1.0]
    assert len(enforced) >= 3
    assert all(s <= 5.0 for s in enforced)


def test_wikipedia_rate_limit_is_thread_safe_single_lock():
    """The limiter uses ONE shared lock/last-call across all callers (not per-thread)."""
    import src.extraction.enrich_biographies as EB

    for attr in ("_lock", "_last_call"):
        if hasattr(EB.search_wikipedia, attr):
            delattr(EB.search_wikipedia, attr)
    EB._wikipedia_rate_limit()
    lock1 = EB.search_wikipedia._lock
    EB._wikipedia_rate_limit()
    lock2 = EB.search_wikipedia._lock
    assert lock1 is lock2  # same shared lock object, not re-created per call


# --------------------------------------------------------------------------- #
# DIVERGENCE FIX (PR #640): an already-enriched-ON-DISK person is SKIPPED by the
# PARALLEL path exactly like serial — no fetch, counted "skipped", and the
# persisted content is BYTE-IDENTICAL to the serial path for the same input.
#
# Root cause fixed: the parallel pre-fetch used to gate _is_already_enriched on
# the RAW pre-merge person (no on-disk bio) => FALSE => a redundant fetch whose
# result _merge_bio_lists folded in => content could differ from serial (which
# gates on merged = _merge_person(on_disk, person) => TRUE => skip, no fetch).
# --------------------------------------------------------------------------- #

_ENRICHED_ON_DISK = {
    "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
    "name": "Dwight Eisenhower",
    "biographical_profile": {
        "birth_date": "1890-10-14",
        "biographical_details": "Supreme Allied Commander, AEF.",
        "biography_sources": [
            {
                "source": "Grokipedia",
                "confidence": 0.95,
                "fields_sourced": ["birth_date"],
            }
        ],
    },
    "event_mentions": [
        {
            "MentionID": "01HX7YZABCDEFGHJKMNPQRSTV0",
            "EventID": "01HX7YZABCDEFGHJKMNPQRSTV1",
            "Sub_eventID": "01HX7YZABCDEFGHJKMNPQRSTV2",
            "Event_Name": "Normandy",
            "Sub-event_Name": "D-Day",
            "role": "commander",
        }
    ],
}


def _raw_mention(sub_id):
    return {
        "MentionID": "01HX7YZABCDEFGHJKMNPQRSTV3",
        "EventID": "01HX7YZABCDEFGHJKMNPQRSTV1",
        "Sub_eventID": sub_id,
        "Event_Name": "Normandy",
        "Sub-event_Name": "D-Day",
        "role": "commander",
    }


def _gated_fetch(fetch_log, lock):
    """A fetch stand-in that honors the SAME gate as the real
    enrich_person_from_sources: it skips an already-enriched record (returns False,
    no mutation) and otherwise records a fetch + mutates in place. Used by BOTH the
    serial and parallel runs so any divergence is the GATE/merge, not the fetch."""
    from src.extraction.enrich_biographies import _is_already_enriched

    def fetch(person, client):
        if _is_already_enriched(person):
            return False
        with lock:
            fetch_log.append(person.get("name"))
        person["biographical_profile"] = {
            "birth_date": "FRESH-1899",
            "biographical_details": "freshly fetched",
        }
        person["enrichment_status"] = "enriched"
        return True

    return fetch


def _run_extract_once(tmp_path, monkeypatch, parallel, fetch):
    """Drive extract_people once over a single already-enriched-on-disk person,
    returning (persisted_bytes, counts, fetched_names). ``parallel`` toggles the
    config kill switch; everything else (input, on-disk record) is identical."""
    import json

    out = tmp_path / "out"
    people_dir = out / "people"
    people_dir.mkdir(parents=True)

    # Seed the on-disk already-enriched record + index (what the serial path merges
    # into and gates on). Plain JSON write keeps the test hermetic.
    name_key = P._normalize_name(_ENRICHED_ON_DISK["name"])
    filename = "eisenhower.json"
    (people_dir / filename).write_text(json.dumps(_ENRICHED_ON_DISK))
    (people_dir / "index.json").write_text(json.dumps({name_key: filename}))

    # Event with ONE sub-event; extraction yields the SAME person (raw, no on-disk
    # bio) that already exists on disk -> the update/merge path.
    event = {
        "Chapter": "Ch1",
        "Event": {"EventID": "E1", "Sub-events": [{"Sub-eventID": "S1"}]},
    }
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))

    raw_person = {
        "name": "Dwight Eisenhower",
        "event_mentions": [_raw_mention("01HX7YZABCDEFGHJKMNPQRSTV9")],
    }

    monkeypatch.setattr(
        P, "_extract_people_for_sub_event", lambda *a, **k: [dict(raw_person)]
    )
    monkeypatch.setattr(P, "_check_if_processed", lambda *a, **k: False)
    monkeypatch.setattr(P, "_load_book_metadata", lambda *a, **k: ("b", "a", "s"))
    monkeypatch.setattr(P, "_record_recall_gaps_safe", lambda *a, **k: None)
    monkeypatch.setattr(P, "_mark_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(P, "observe_yield", lambda *a, **k: None)
    monkeypatch.setattr(P, "warn_all_subevents_empty", lambda *a, **k: None)
    # Deterministic id mint (the on-disk record already has a PersonID; merge keeps it).
    monkeypatch.setattr(P, "_mint_person_ids", lambda person, pid: None)

    # should_parallelize requires >1 person normally; force the gate purely on the
    # kill switch so we can exercise the parallel path with a single already-enriched
    # person (the thing we are regression-testing).
    monkeypatch.setattr(
        BP, "should_parallelize", lambda people, client, config: parallel
    )

    cfg = {
        "concurrency": {
            "max_enrichment_workers": 4,
            "bio_enrichment_parallel": parallel,
        }
    }
    import src.utils.config as cfgmod

    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: cfg)

    import src.extraction.enrich_biographies as EB

    monkeypatch.setattr(EB, "enrich_person_from_sources", fetch)

    # Hermetic persist: replace the locked save with a plain JSON read-modify-write
    # so no DynamoDB/S3 dual-write runs and the content is byte-comparable.
    import contextlib

    @contextlib.contextmanager
    def fake_locked_json(filepath):
        data = {}
        if filepath.exists():
            data = json.loads(filepath.read_text())

        def save(new_data):
            filepath.write_text(
                json.dumps(new_data, indent=2, ensure_ascii=False, sort_keys=True)
            )

        yield data, save

    import src.utils.file_lock as FL

    monkeypatch.setattr(FL, "locked_json", fake_locked_json)
    monkeypatch.setattr(
        P,
        "_save_person_file",
        lambda pf, pdata: pf.write_text(
            json.dumps(pdata, indent=2, ensure_ascii=False, sort_keys=True)
        ),
    )

    P.extract_people(event_file, _FakeGrok(), out)

    persisted_bytes = (people_dir / filename).read_text()
    return persisted_bytes, dict(_counts()), fetch


def test_already_enriched_on_disk_skipped_in_parallel_matches_serial(
    tmp_path, monkeypatch
):
    """The blocking divergence regression: parallel must SKIP an already-enriched-on-disk
    person (no fetch, counted skipped) and persist content byte-identical to serial."""
    # --- SERIAL run (kill switch off) ---
    serial_fetches = []
    serial_fetch = _gated_fetch(serial_fetches, threading.Lock())
    serial_bytes, serial_counts, _ = _run_extract_once(
        tmp_path / "serial", monkeypatch, parallel=False, fetch=serial_fetch
    )

    # --- PARALLEL run (kill switch on), same input/on-disk record ---
    parallel_fetches = []
    parallel_fetch = _gated_fetch(parallel_fetches, threading.Lock())
    parallel_bytes, parallel_counts, _ = _run_extract_once(
        tmp_path / "parallel", monkeypatch, parallel=True, fetch=parallel_fetch
    )

    # No redundant fetch on EITHER path for the already-enriched person.
    assert serial_fetches == [], "serial must not fetch an already-enriched person"
    assert parallel_fetches == [], (
        "PARALLEL must not redundantly fetch an already-enriched-on-disk person "
        "(the PR #640 divergence)"
    )

    # Counts match: both count it total=1, skipped=1, enriched=0.
    assert serial_counts["enriched"] == 0
    assert serial_counts["skipped"] == 1
    assert parallel_counts["enriched"] == serial_counts["enriched"]
    assert parallel_counts["skipped"] == serial_counts["skipped"]
    assert parallel_counts["total"] == serial_counts["total"]

    # CONTENT byte-identical: the on-disk bio is preserved unchanged on both paths
    # (no FRESH-1899 contamination from a redundant fetch+merge).
    assert "FRESH-1899" not in parallel_bytes
    assert parallel_bytes == serial_bytes
