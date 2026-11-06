"""Shared Wikipedia/Wikimedia client: a rate limit costs seconds, not minutes, and never becomes
"not found" for ANY entity type.

Found in the XChannel-8 dev E2E (2026-11-06): every Wikipedia bio lookup after the first got HTTP 429
with Retry-After: 60. The default pooled session retried each 429 three times, sleeping the full
Retry-After (180 s per lookup), then raised RetryError. The person was then stamped
enrichment_status=not_found (90-day suppression); groups, equipment and places stamped
wikipedia_checked_at ("never re-fetch") the same way. All 11 Wikimedia call sites now go through
src.utils.wikimedia_client; the last test keeps it that way.

The transport tests intercept urllib3 below the session (no real network), so they exercise the real
Retry configuration, which is where the bug lived.
"""

import ast
import io
import time
from pathlib import Path

import pytest
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.response import HTTPResponse

from src.utils import http_pool
from src.utils import wikimedia_client as WC

REPO = Path(__file__).resolve().parents[1]


class _MemCache:
    """search_cache backend stand-in: the REAL cache_result/get_cached logic runs on top of it."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value, ttl_days=None):
        self.data[key] = value


@pytest.fixture
def wire(monkeypatch):
    """Fake HTTP server below urllib3: set .status / .retry_after / .body; .calls counts requests."""

    class Wire:
        status = 429
        retry_after = "60"
        body = b"rate limited"
        calls = 0
        script: list = (
            []
        )  # optional per-request (status, retry_after, body), consumed in order

    def fake_make_request(self, conn, method, url, body=None, headers=None, **kw):
        Wire.calls += 1
        status, retry_after, payload = (
            Wire.script.pop(0)
            if Wire.script
            else (Wire.status, Wire.retry_after, Wire.body)
        )
        hdrs = {"Content-Type": "application/json"}
        if retry_after is not None:
            hdrs["Retry-After"] = retry_after
        return HTTPResponse(
            body=io.BytesIO(payload),
            status=status,
            headers=hdrs,
            preload_content=False,
            request_method=method,
            request_url=url,
        )

    monkeypatch.setattr(HTTPConnectionPool, "_make_request", fake_make_request)
    http_pool.close_session()
    WC.reset_state()
    monkeypatch.setattr("src.utils.fetch_provenance._table", lambda: None)
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda *a, **k: None)
    monkeypatch.setattr("src.utils.search_cache.cache_result", lambda *a, **k: None)
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    yield Wire
    http_pool.close_session()
    WC.reset_state()


def _get(subject="Dwight D. Eisenhower"):
    return WC.wikimedia_get(
        "wikipedia",
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "titles": subject},
        subject=subject,
    )


# --------------------------------------------------------------------------- transport


def test_default_session_still_retries_429_for_non_wikimedia_callers(wire):
    wire.retry_after = "0"
    with pytest.raises(Exception):
        http_pool.get_session().get("https://example.org/x", timeout=5)
    assert wire.calls == 4  # 1 + 3 retries: unchanged for every other provider


def test_rate_limit_session_returns_429_even_with_retry_after(wire):
    """status_forcelist alone is not enough: urllib3 retries 413/429/503 WITH a Retry-After
    header regardless of the forcelist (RETRY_AFTER_STATUS_CODES)."""
    resp = http_pool.get_session(retry_rate_limited=False).get(
        "https://example.org/x", timeout=5
    )
    assert resp.status_code == 429 and wire.calls == 1


def test_rate_limit_session_returns_503_even_with_retry_after(wire):
    wire.status = 503
    resp = http_pool.get_session(retry_rate_limited=False).get(
        "https://example.org/x", timeout=5
    )
    assert resp.status_code == 503 and wire.calls == 1


def test_rate_limit_session_still_retries_502(wire):
    wire.status, wire.retry_after = 502, None
    with pytest.raises(Exception):
        http_pool.get_session(retry_rate_limited=False).get(
            "https://example.org/x", timeout=5
        )
    assert wire.calls == 4


# --------------------------------------------------------------------------- client policy


def test_long_retry_after_returns_transient_immediately(wire):
    """Production shape (Retry-After 60): one request, no sleep."""
    t0 = time.monotonic()
    assert _get() is WC.TRANSIENT
    assert time.monotonic() - t0 < 3 and wire.calls == 1


def test_backoff_window_sends_no_further_requests(wire):
    """Polite: while Retry-After has not passed, no request is sent to that host."""
    assert _get("A") is WC.TRANSIENT
    assert _get("B") is WC.TRANSIENT
    assert _get("C") is WC.TRANSIENT
    assert wire.calls == 1


def test_backoff_is_per_host(wire):
    _get()  # en.wikipedia.org now backing off
    wire.status, wire.retry_after = 200, None
    wire.body = b'{"query": {}}'
    resp = WC.wikimedia_get(
        "wikimedia-commons", "https://commons.wikimedia.org/w/api.php", subject="f"
    )
    assert resp is not WC.TRANSIENT and resp.status_code == 200


def test_short_retry_after_is_honored_once(wire):
    wire.retry_after = "1"
    t0 = time.monotonic()
    assert _get() is WC.TRANSIENT
    assert 1.0 <= time.monotonic() - t0 < 4 and wire.calls == 2


@pytest.mark.parametrize("status", [403, 503])
def test_block_and_server_errors_are_transient(wire, status):
    wire.status, wire.retry_after = status, None
    assert _get() is WC.TRANSIENT


def test_genuine_200_and_404_are_returned(wire):
    wire.status, wire.retry_after, wire.body = 200, None, b'{"query": {}}'
    assert _get().status_code == 200
    wire.status = 404
    assert _get().status_code == 404


def test_transient_is_loud(wire, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    _get()
    assert any(
        "OBSERV wikimedia.transient status=warn" in r.message
        and "reason=rate_limited" in r.message
        for r in caplog.records
    )


def test_injected_session_is_used(wire):
    from unittest import mock

    sess = mock.Mock()
    sess.request.return_value = mock.Mock(status_code=200, headers={})
    assert _get_with(sess).status_code == 200
    assert sess.request.called and wire.calls == 0


def test_injected_default_session_is_swapped(wire):
    """situational_geocode threads the DEFAULT pooled session through; it must not bring back
    urllib3's 3 x Retry-After stall."""
    t0 = time.monotonic()
    assert _get_with(http_pool.get_session()) is WC.TRANSIENT
    assert time.monotonic() - t0 < 3 and wire.calls == 1


def test_rate_limit_session_ignores_retry_after_on_5xx(wire):
    wire.status, wire.retry_after = 502, "120"
    t0 = time.monotonic()
    with pytest.raises(Exception):
        http_pool.get_session(retry_rate_limited=False).get(
            "https://example.org/x", timeout=5
        )
    assert time.monotonic() - t0 < 20 and wire.calls == 4


def _get_with(sess):
    return WC.wikimedia_get(
        "wikipedia", "https://en.wikipedia.org/w/api.php", subject="x", session=sess
    )


def test_lookup_was_transient_clears_flag():
    WC.reset_state()
    WC.mark_transient(WC.PEOPLE, "Omar N. Bradley", "lookup")
    assert WC.lookup_was_transient(WC.PEOPLE, "Omar N. Bradley", None) is True
    assert (
        WC.lookup_was_transient(WC.PEOPLE, "Omar N. Bradley", None) is False
    )  # consumed
    WC.mark_transient(WC.PEOPLE, "Omar N. Bradley", "lookup")
    assert WC.lookup_was_transient(WC.PEOPLE, "Omar N. Bradley", {"hit": 1}) is False


def test_namespaces_do_not_collide():
    """A person, a unit and a place can share a name; one must not clear another's flag."""
    WC.reset_state()
    WC.mark_transient(WC.PLACES, "Patton", "lookup")
    assert WC.lookup_was_transient(WC.PEOPLE, "Patton", None) is False
    assert WC.lookup_was_transient(WC.PLACES, "Patton", None) is True


def test_deferred_set_is_bounded(monkeypatch):
    WC.reset_state()
    monkeypatch.setattr(WC, "MAX_DEFERRED", 3)
    for i in range(10):
        WC.mark_transient(WC.PEOPLE, f"P{i}", "lookup")
    assert len(WC._deferred) <= 3


def test_503_is_handled_like_a_rate_limit(wire):
    """A 503 with Retry-After: 60 must not cost 3 x 60 s inside urllib3 either."""
    wire.status = 503
    t0 = time.monotonic()
    assert _get() is WC.TRANSIENT
    assert time.monotonic() - t0 < 3 and wire.calls == 1


def test_bare_503_does_not_back_the_host_off(wire):
    """No Retry-After = no instruction to wait: one overloaded response must not silence the host."""
    wire.script = [(503, None, b"")]
    wire.status, wire.retry_after, wire.body = 200, None, b'{"query": {}}'
    assert _get("A") is WC.TRANSIENT
    assert _get("B").status_code == 200


def test_stored_backoff_is_capped(wire):
    wire.retry_after = "999999999"
    _get()
    assert WC._backoff_remaining("en.wikipedia.org") <= WC.MAX_BACKOFF_SECONDS


def test_403_backs_the_host_off(wire):
    wire.status, wire.retry_after = 403, None
    assert _get() is WC.TRANSIENT
    assert _get() is WC.TRANSIENT
    assert wire.calls == 1  # second call held back by the block back-off


def test_threads_wait_out_a_short_backoff_instead_of_deferring(wire):
    """Bounded-parallel Phase 2: one short 429 must not defer every other worker."""
    import threading

    wire.script = [(429, "1", b"")]
    wire.status, wire.retry_after, wire.body = 200, None, b'{"query": {}}'
    results = []

    def worker(n):
        results.append(_get(f"Person {n}"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    threads[0].start()
    time.sleep(0.2)  # first worker hits the 429 and records the 1 s back-off
    for t in threads[1:]:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 4
    assert all(r is not WC.TRANSIENT and r.status_code == 200 for r in results)


# --------------------------------------------------------------------------- cached_variant_search


@pytest.fixture
def variant(monkeypatch):
    import threading as _t

    import src.utils.search_cache as SC
    from src.enrichment._wikipedia_common import cached_variant_search

    mem = _MemCache()
    monkeypatch.setattr(SC, "_cache_backend", mem)
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    WC.reset_state()
    images: dict = {}

    def run(outcomes):
        seq = iter(outcomes)
        return cached_variant_search(
            "wikipedia_group",
            "1st Infantry Division",
            lambda title: next(seq),
            ["a", "b", "c"],
            images,
            _t.Lock(),
        )

    return run, mem, images


def test_variant_hit_after_transient_is_cached(variant):
    run, mem, images = variant
    hit = {"wikipedia_url": "u", "extract": "e", "image": "i.jpg"}
    assert run([WC.TRANSIENT, hit]) == hit
    assert any("NOT_FOUND" != v for v in mem.data.values())
    assert images["1st Infantry Division"]["url"] == "i.jpg"
    assert WC.lookup_was_transient(WC.GROUPS, "1st Infantry Division", hit) is False


def test_variant_transient_then_misses_is_deferred_not_cached(variant):
    run, mem, _ = variant
    assert run([WC.TRANSIENT, None, None]) is None
    assert mem.data == {}  # nothing cached
    assert WC.lookup_was_transient(WC.GROUPS, "1st Infantry Division", None) is True


def test_variant_all_genuine_misses_are_negative_cached(variant):
    run, mem, _ = variant
    assert run([None, None, None]) is None
    assert list(mem.data.values()) == ["NOT_FOUND"]
    assert WC.lookup_was_transient(WC.GROUPS, "1st Infantry Division", None) is False


# --------------------------------------------------------------------------- per-entity stamping
# A transient lookup leaves the record unstamped; a genuine miss is still stamped.


class _Grok:
    batch_mode = False


@pytest.mark.parametrize("transient", [True, False])
def test_people_not_found_stamp(wire, monkeypatch, transient):
    import src.extraction.enrich_biographies as EB

    def fake_steps(name, bio, grok, search_references_flag):
        if transient:
            WC.mark_transient(WC.PEOPLE, name, "lookup")
        return False

    monkeypatch.setattr(EB, "_run_enrichment_steps", fake_steps)
    monkeypatch.setattr(EB, "_is_already_enriched", lambda p: False)
    person = {"name": "Dwight D. Eisenhower"}
    changed = EB.enrich_person_from_sources(person, _Grok())
    if transient:
        assert changed is False and "enrichment_status" not in person
    else:
        assert changed is True and person["enrichment_status"] == "not_found"


@pytest.mark.parametrize("transient", [True, False])
def test_groups_phase2_checked_stamp(wire, monkeypatch, transient):
    import src.enrichment.groups_wikipedia as GW

    monkeypatch.setattr(
        "src.enrichment.grokipedia.resolve_grokipedia_url", lambda n: None
    )

    def fake_search(name):
        if transient:
            WC.mark_transient(WC.GROUPS, name, "lookup")
        return None

    monkeypatch.setattr(GW, "search_group_wikipedia", fake_search)
    group = {"group_name": "1st Infantry Division"}
    GW.enrich_group_from_wikipedia(group)
    assert ("wikipedia_checked_at" in group) is (not transient)


@pytest.mark.parametrize("transient", [True, False])
def test_groups_phase3_checked_stamp(wire, monkeypatch, tmp_path, transient):
    import json

    import src.enrichment.groups_wikipedia as GW

    def fake_search(name):
        if transient:
            WC.mark_transient(WC.GROUPS, name, "lookup")
        return None

    stamped = []
    monkeypatch.setattr(GW, "search_group_wikipedia", fake_search)
    monkeypatch.setattr(
        "src.enrichment.enrich_write.update_enriched",
        lambda f, entity, fn: stamped.append(fn({})),
    )
    f = tmp_path / "g.json"
    f.write_text(json.dumps({"group_name": "1st Infantry Division"}))
    GW._enrich_one_group_file(f)
    assert bool(stamped) is (not transient)


@pytest.mark.parametrize("transient", [True, False])
def test_equipment_phase2_checked_stamp(wire, monkeypatch, transient):
    import src.extraction.equipment as EQ

    monkeypatch.setattr(EQ, "_apply_grokipedia_url", lambda d, n: None)

    def fake_search(name):
        if transient:
            WC.mark_transient(WC.EQUIPMENT, name, "lookup")
        return None

    monkeypatch.setattr(
        "src.enrichment.equipment_wikipedia.search_equipment_wikipedia", fake_search
    )
    rec = {}
    EQ._apply_wikipedia_text_extract(rec, "M4 Sherman")
    assert ("wikipedia_checked_at" in rec) is (not transient)


@pytest.mark.parametrize("transient", [True, False])
def test_equipment_phase3_checked_stamp(wire, monkeypatch, tmp_path, transient):
    import json

    import src.enrichment.equipment_wikipedia as EW

    def fake_search(name):
        if transient:
            WC.mark_transient(WC.EQUIPMENT, name, "lookup")
        return None

    stamped = []
    monkeypatch.setattr(EW, "search_equipment_wikipedia", fake_search)
    monkeypatch.setattr(
        "src.enrichment.enrich_write.update_enriched",
        lambda f, entity, fn: stamped.append(fn({})),
    )
    f = tmp_path / "e.json"
    f.write_text(json.dumps({"common_name": "M4 Sherman"}))
    EW._enrich_one_equipment_file(f)
    assert bool(stamped) is (not transient)


@pytest.mark.parametrize("transient", [True, False])
def test_places_checked_stamp(wire, monkeypatch, transient):
    import src.extraction.enrich_places as EP

    def fake_fetch(name):
        if transient:
            WC.mark_transient(WC.PLACES, name, "lookup")
        return None

    monkeypatch.setattr(EP, "_fetch_place_wikipedia_full", fake_fetch)
    place = {"current_name": "Carentan"}
    EP.enrich_place_from_wikipedia(place)
    assert ("wikipedia_checked_at" in place) is (not transient)


def test_group_lookup_marks_transient_end_to_end(wire):
    """Real search_group_wikipedia over the fake wire: a 429 marks the group, no stamp follows."""
    import src.enrichment.groups_wikipedia as GW

    assert GW.search_group_wikipedia("2nd Armored Division") is None
    assert WC.lookup_was_transient(WC.GROUPS, "2nd Armored Division", None) is True


# --------------------------------------------------------------------------- end to end, real cache


@pytest.fixture
def real_cache(monkeypatch):
    import src.utils.search_cache as SC

    mem = _MemCache()
    monkeypatch.setattr(SC, "_cache_backend", mem)
    # undo the wire fixture's no-op stubs so the real cache logic runs
    monkeypatch.setattr(SC, "get_cached", _REAL_GET)
    monkeypatch.setattr(SC, "cache_result", _REAL_PUT)
    return mem


import src.utils.search_cache as _SC  # noqa: E402

_REAL_GET, _REAL_PUT = _SC.get_cached, _SC.cache_result


_MISSING_PAGE = b'{"query": {"pages": {"-1": {"title": "X", "missing": ""}}}}'


def test_people_fallback_rate_limit_is_not_negative_cached(
    wire, real_cache, monkeypatch
):
    """Review blocker: direct title misses (200, no page), then the search fallback is rate
    limited. The person must be deferred, and the search cache must NOT hold NOT_FOUND (which would
    make the NEXT run stamp not_found)."""
    import src.extraction.enrich_biographies as EB
    import src.utils.search_cache as SC

    monkeypatch.setattr(EB, "_wikipedia_rate_limit", lambda: None)
    wire.script = [(200, None, _MISSING_PAGE), (429, "60", b"")]
    assert EB.search_wikipedia("Bertram H. Ramsay") is None
    assert SC.get_cached("wikipedia", "Bertram H. Ramsay") != "NOT_FOUND"
    assert WC.lookup_was_transient(WC.PEOPLE, "Bertram H. Ramsay", None) is True


def test_people_genuine_miss_is_negative_cached(wire, real_cache, monkeypatch):
    import src.extraction.enrich_biographies as EB
    import src.utils.search_cache as SC

    monkeypatch.setattr(EB, "_wikipedia_rate_limit", lambda: None)
    empty_search = b'{"query": {"search": []}}'
    wire.script = [
        (200, None, _MISSING_PAGE),
        (200, None, empty_search),
        (200, None, empty_search),
    ]
    assert EB.search_wikipedia("Private Nobody Xyzzy") is None
    assert SC.get_cached("wikipedia", "Private Nobody Xyzzy") == "NOT_FOUND"
    assert WC.lookup_was_transient(WC.PEOPLE, "Private Nobody Xyzzy", None) is False


# --------------------------------------------------------------------------- DRY guard


_WIKI_HOSTS = ("wikipedia.org", "wikimedia.org")
_HTTP_FUNCS = {"recorded_fetch", "get", "post", "request", "urlopen"}


def _module_constants(tree):
    """Module-level NAME = "string" assignments (endpoint constants like _WIKI_API)."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name) and isinstance(node.value.value, str):
                    out[t.id] = node.value.value
    return out


def _call_targets_wikimedia(node, consts):
    """True if an HTTP-ish call names a Wikimedia provider or a Wikimedia host (literal,
    f-string part or module constant), positionally or by keyword."""
    values = list(node.args) + [k.value for k in node.keywords]
    for v in values:
        if isinstance(v, ast.Name):
            v = ast.Constant(consts.get(v.id))
        texts = []
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            texts = [v.value]
        elif isinstance(v, ast.JoinedStr):
            texts = [
                p.value
                for p in v.values
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            ]
        for text in texts:
            if text in WC.WIKIMEDIA_PROVIDERS or any(h in text for h in _WIKI_HOSTS):
                return True
    return False


def test_no_wikimedia_http_outside_client():
    """Every Wikipedia/Wikimedia API call goes through src.utils.wikimedia_client (DRY): no
    recorded_fetch / session.get / requests.* call names a Wikimedia provider or host, whether as
    a literal, keyword, f-string or module constant."""
    offenders = []
    for path in (REPO / "src").rglob("*.py"):
        if path.name == "wikimedia_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            if name in _HTTP_FUNCS and _call_targets_wikimedia(node, consts):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, f"use wikimedia_client.wikimedia_get: {offenders}"


def test_guard_catches_known_bypass_shapes(tmp_path):
    """The guard itself must catch keyword, constant and f-string forms."""
    src = (
        'API = "https://en.wikipedia.org/w/api.php"\n'
        "recorded_fetch(provider='wikipedia', endpoint='x')\n"
        "session.get(API)\n"
        "requests.get(f'https://commons.wikimedia.org/{x}')\n"
    )
    tree = ast.parse(src)
    consts = _module_constants(tree)
    hits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_targets_wikimedia(n, consts)
    ]
    assert len(hits) == 3
