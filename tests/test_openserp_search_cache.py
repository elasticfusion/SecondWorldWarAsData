"""OpenSERP answers are kept per (query, limit, start) for ``openserp_cache_days``: a batch
round, a retry or another record asking the same thing sends no query (Group 3)."""

from unittest import mock

import pytest

import src.enrichment.openserp_enrichment as oe


@pytest.fixture
def serp(monkeypatch):
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"openserp": {"rate_limit_seconds": 5, "results_per_query": 5}},
    )
    sleeps, sent, replies = [], [], []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    oe.reset_circuit()
    oe.reset_metrics()

    def fetch(*a, **k):
        sent.append(dict(k["params"]))
        return replies.pop(0) if replies else _reply([])

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fetch)
    yield sent, replies, sleeps
    oe.reset_metrics()


def _reply(urls, status=200):
    body = {"results": [{"url": u, "title": "t", "snippet": "s"} for u in urls]}
    return mock.Mock(status_code=status, json=lambda: body)


def _traced(query):
    trace = oe._new_trace("people", "f", {}, "x")
    with oe._tracing(trace):
        out = oe._search_openserp(query, "http://serp")
    return out, [q["status"] for q in trace["queries"]]


def test_the_same_query_is_sent_once(serp):
    sent, replies, sleeps = serp
    replies.append(_reply(["https://a.org/1", "https://a.org/2"]))
    first, s1 = _traced("Patton WWII biography")
    again, s2 = _traced("Patton WWII biography")
    assert first == again and len(first) == 2
    assert len(sent) == 1 and sleeps == [
        5
    ]  # no second query, no second rate-limit wait
    assert s1 == s2 == ["results"]  # the entity's outcome reads the same
    snap = oe.get_metrics()
    assert snap["queries_issued"] == 1 and snap["queries_cached"] == 1


def test_an_empty_answer_is_kept_too(serp):
    sent, replies, _ = serp
    assert _traced("nobody at all")[1] == ["empty"]
    assert _traced("nobody at all")[1] == ["empty"]
    assert len(sent) == 1


def test_a_failed_search_is_not_kept(serp):
    sent, replies, _ = serp
    replies.extend([_reply([], status=503), _reply(["https://a.org/1"])])
    assert _traced("q")[1] == ["error"]
    out, statuses = _traced("q")
    assert statuses == ["results"] and len(out) == 1 and len(sent) == 2


def test_each_result_page_is_its_own_answer(serp):
    sent, replies, _ = serp
    oe._search_openserp("q", "http://serp", start=0)
    oe._search_openserp("q", "http://serp", start=5)
    assert len(sent) == 2 and sent[1]["start"] == "5"


def test_a_domain_excluded_after_the_answer_was_kept_is_still_dropped(
    serp, monkeypatch
):
    sent, replies, _ = serp
    replies.append(_reply(["https://spam.example/x", "https://good.org/y"]))
    assert len(_traced("q")[0]) == 2
    monkeypatch.setattr(
        "src.utils.url_registry.is_domain_excluded", lambda url: "spam.example" in url
    )
    assert [r["url"] for r in _traced("q")[0]] == ["https://good.org/y"]


def test_answers_keep_for_the_scraped_engine_window(serp, monkeypatch):
    from src.utils import search_cache

    kept = {}
    real = search_cache._cache_backend

    class _Spy:
        def get(self, k):
            return real.get(k)

        def put(self, k, v, ttl_days=30):
            kept[k] = ttl_days
            real.put(k, v, ttl_days=ttl_days)

    monkeypatch.setattr(search_cache, "_cache_backend", _Spy())
    oe._search_openserp("q", "http://serp")
    assert list(kept.values()) == [search_cache.scraped_ttl_days()]


def test_a_200_that_is_not_an_answer_counts_toward_the_breaker(serp, monkeypatch):
    from src.utils import grok_models

    sent, replies, _ = serp
    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: False)
    for n in range(oe._CIRCUIT_BREAKER_THRESHOLD - 1):
        bad = mock.Mock(status_code=200)
        bad.json.side_effect = ValueError("captcha page, not JSON")
        replies.append(bad)
        assert _traced(f"q{n}")[1] == ["error"]
    bad = mock.Mock(status_code=200)
    bad.json.side_effect = ValueError("captcha page, not JSON")
    replies.append(bad)
    with pytest.raises(
        grok_models.ServiceUnreachable
    ):  # the breaker opened: run stopped
        _traced("q-last")


def test_a_corrupt_cached_answer_is_asked_again(serp):
    from src.utils import search_cache

    sent, replies, _ = serp
    key = search_cache._make_key(f"answer#{oe._SEARCH_CACHE_SOURCE}", '["q", 5, 0]')
    search_cache._get_backend().put(key, '[1, "x"]', ttl_days=1)
    replies.append(_reply(["https://a.org/1"]))
    out, statuses = _traced("q")
    assert statuses == ["results"] and len(out) == 1 and len(sent) == 1
    assert oe.get_metrics()["queries_cache_unusable"] == 1


def test_cached_queries_count_in_the_zero_result_rate(serp):
    _traced("nobody")
    _traced("nobody")
    snap = oe.get_metrics()
    assert snap["zero_result_rate"] == 1.0  # 2 empty of 2 asked (1 sent, 1 cached)


def test_the_first_page_is_one_entry_whatever_the_limit_spelling(serp):
    sent, replies, _ = serp
    oe._search_openserp("q", "http://serp")
    oe._search_openserp("q", "http://serp", limit=5, start=0)
    assert len(sent) == 1


def test_an_error_envelope_is_a_failure_not_an_empty_answer(serp):
    sent, replies, _ = serp
    replies.extend(
        [
            mock.Mock(status_code=200, json=lambda: {"error": "captcha"}),
            _reply(["https://a.org/1"]),
        ]
    )
    assert _traced("q")[1] == ["error"]
    assert _traced("q")[1] == ["results"]  # not kept: asked again
