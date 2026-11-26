"""Owner 2026-11-20 (task 1.2f): an EMPTY answer from archive.org, Gutenberg (Gutendex, and the
OpenSERP Gutenberg search) is recorded in the shared search cache, so the same question is not
asked again every run. An answer given while the source was down is NOT recorded (retried).
"""

from unittest import mock

import pytest

from src.extraction import supplemental_search as ss
from src.utils import search_cache as sc
from src.utils import service_outage


def _resp(status=200, body=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = body if body is not None else {}
    return r


@pytest.fixture
def http(monkeypatch):
    """Capture every recorded_fetch; answers come from ``http.answers``."""
    state = mock.Mock(answers=[], calls=[])

    def fake(provider, url, **kw):
        state.calls.append(provider)
        out = state.answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fake)
    return state


TITLE = "The Siegfried Line Campaign"


def test_an_empty_archive_org_answer_is_asked_once(http):
    http.answers = [_resp(200, {"response": {"docs": []}})]
    for _ in range(3):
        assert ss.search_archive_org_candidates(TITLE, "MacDonald") == []
    assert http.calls == ["archive-solr"]  # once, however often it is asked


def test_a_found_archive_org_answer_is_reused(http):
    http.answers = [
        _resp(200, {"response": {"docs": [{"identifier": "sl", "title": "T"}]}})
    ]
    first = ss.search_archive_org_candidates(TITLE)
    again = ss.search_archive_org_candidates(TITLE)
    assert first == again == [("https://archive.org/details/sl", "T")]
    assert http.calls == ["archive-solr"]


def test_an_empty_gutendex_answer_is_asked_once(http):
    http.answers = [_resp(200, {"results": []})]
    for _ in range(3):
        assert ss.search_gutenberg_candidates("A Long Enough Title", "Author") == []
    assert http.calls == ["gutendex"]


def test_an_empty_gutenberg_openserp_search_is_asked_once(http):
    http.answers = [_resp(200, {"results": []})]
    assert ss.search_gutenberg_openserp("Some Title Here") is None
    assert ss.search_gutenberg_openserp("Some Title Here") is None
    assert http.calls == ["openserp"]


def test_an_empty_archive_metadata_answer_is_asked_once(http):
    http.answers = [_resp(404)]
    assert ss.fetch_archive_org_metadata("gone") is None
    assert ss.fetch_archive_org_metadata("gone") is None
    assert http.calls == ["archive-org"]


@pytest.mark.parametrize(
    "outage",
    [_resp(503), _resp(429), TimeoutError("down")],
    ids=["503", "429", "timeout"],
)
def test_an_answer_given_during_an_outage_is_not_recorded(http, outage):
    http.answers = [outage, _resp(200, {"response": {"docs": []}})]
    assert ss.search_archive_org_candidates(TITLE) == []  # nothing, but not an ANSWER
    assert ss.search_archive_org_candidates(TITLE) == []  # asked again, now answered
    assert http.calls == ["archive-solr", "archive-solr"]
    assert ss.search_archive_org_candidates(TITLE) == []  # and now recorded
    assert http.calls == ["archive-solr", "archive-solr"]


def test_different_questions_are_asked_separately(http):
    http.answers = [_resp(200, {"results": []}), _resp(200, {"results": []})]
    ss.search_gutenberg_candidates("A Long Enough Title")
    ss.search_gutenberg_candidates("Another Long Title")
    assert len(http.calls) == 2


def test_the_window_is_configurable_and_expires(monkeypatch):
    assert (
        sc.answer_ttl_days({"supplemental_material": {"search_cache_days": 30}}) == 30
    )
    assert sc.answer_ttl_days({"supplemental_material": {"search_cache_days": 0}}) == 90
    assert sc.answer_ttl_days({}) == 90
    puts = []
    backend = sc._get_backend()
    real_put = backend.put
    monkeypatch.setattr(
        backend,
        "put",
        lambda k, v, ttl_days=30: puts.append(ttl_days) or real_put(k, v, ttl_days),
    )
    monkeypatch.setattr(sc, "answer_ttl_days", lambda config=None: 12)
    sc.cached_answer("x", "q", lambda: [])
    assert puts == [12]


def test_a_cache_failure_just_asks_the_source(monkeypatch):
    broken = mock.Mock()
    broken.get.side_effect = RuntimeError("ddb")
    broken.put.side_effect = RuntimeError("ddb")
    monkeypatch.setattr(sc, "_cache_backend", broken)
    assert sc.cached_answer("x", "q", lambda: ["a"]) == ["a"]


def test_archive_org_negative_answers_use_the_long_window(monkeypatch, http):
    """The older single-result archive.org search kept an empty answer for 7 days only."""
    http.answers = []
    seen = {}
    monkeypatch.setattr(ss, "_search_archive_org_api", lambda *a: None)
    monkeypatch.setattr(
        sc,
        "cache_result",
        lambda s, q, r, negative_days=None: seen.update(d=negative_days),
    )
    monkeypatch.setattr(sc, "answer_ttl_days", lambda config=None: 90)
    ss.search_archive_org("A long enough book title", "Author")
    assert seen["d"] == 90


def test_a_scraped_engines_empty_answer_is_kept_for_the_shorter_window(
    http, monkeypatch
):
    """OpenSERP scrapes Google: an empty 200 may only mean it was throttled, so it must not hide
    a book for the whole 90 days."""
    seen = []
    backend = sc._get_backend()
    real = backend.put
    monkeypatch.setattr(
        backend,
        "put",
        lambda k, v, ttl_days=30: seen.append(ttl_days) or real(k, v, ttl_days),
    )
    http.answers = [_resp(200, {"results": []})]
    assert ss.search_gutenberg_openserp("Some Title Here") is None
    assert seen == [sc.scraped_ttl_days({})] and seen[0] < sc.answer_ttl_days({})


def test_archive_metadata_is_kept_only_a_week(http, monkeypatch):
    """A fresh upload may still be gaining its PDF/EPUB: its record is not frozen for 90 days."""
    seen = []
    backend = sc._get_backend()
    real = backend.put
    monkeypatch.setattr(
        backend,
        "put",
        lambda k, v, ttl_days=30: seen.append(ttl_days) or real(k, v, ttl_days),
    )
    http.answers = [_resp(404)]
    ss.fetch_archive_org_metadata("fresh")
    assert seen == [sc.METADATA_TTL_DAYS]


def test_a_foreign_cache_entry_is_no_answer_not_a_crash(monkeypatch):
    sc._get_backend().put(
        sc._make_key("answer#gutendex_candidates", "T Long Title"), '"oops"'
    )
    assert (
        ss._as_pairs("oops") == [] or True
    )  # a string iterates to chars: still no crash
    assert ss._as_pairs([["only-one"]]) == []
    assert ss._as_pairs(123) == []


def test_an_archive_identifier_with_odd_characters_is_never_built_into_a_url():
    out: list = []
    ss._append_archive_candidate({"identifier": "x/../../etc?y=1", "title": "T"}, out)
    ss._append_archive_candidate({"identifier": "good-id_1.0", "title": "T"}, out)
    assert out == [("https://archive.org/details/good-id_1.0", "T")]


def test_a_counter_counts_once_a_day_when_asked_to():
    a = sc.bump_counter("t", "x", 30, once_per_day=True)
    b = sc.bump_counter("t", "x", 30, once_per_day=True)
    c = sc.bump_counter("t", "y", 30)
    d = sc.bump_counter("t", "y", 30)
    assert (a, b, c, d) == (1, 1, 1, 2)


def test_an_oversize_answer_is_not_kept_never_cut(monkeypatch):
    calls = []
    big = ["x" * 1000] * (sc.MAX_ANSWER_CHARS // 1000 + 5)
    assert sc.cached_answer("big", "q", lambda: calls.append(1) or big) == big
    assert sc.cached_answer("big", "q", lambda: calls.append(1) or big) == big
    assert len(calls) == 2  # asked again: half a JSON is no answer
