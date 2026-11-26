"""Tests for OpenSERP positive-URL processing: fetch+summarize + positive/negative URL cache."""

from unittest.mock import MagicMock, patch

import pytest

import src.enrichment.openserp_enrichment as oe


class _Backend:
    """In-memory stand-in for the search-cache backend."""

    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def put(self, k, v, ttl_days=30):
        self.store[k] = v


@pytest.fixture
def backend():
    b = _Backend()
    with (
        patch("src.utils.search_cache._get_backend", return_value=b),
        patch("src.utils.search_cache._make_key", side_effect=lambda s, q: f"{s}:{q}"),
    ):
        yield b


def test_positive_url_fetched_summarized_and_cached(backend):
    gc = MagicMock()
    gc.chat_completion.return_value = "Summary of the page."
    from src.utils import url_fetch

    live = url_fetch.FetchResult(
        url="http://good",
        source=url_fetch.LIVE,
        content="<p>Content.</p>",
        final_url="http://good",
        http_status=200,
    )
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch("src.utils.url_fetch.fetch", return_value=live),
        patch("src.utils.url_registry.finalize_searched", return_value="http://good"),
    ):
        r = oe.process_positive_url("http://good", "T", "snip", "ctx", gc)
    assert r["url"] == "http://good"
    assert r["summary"] == "Summary of the page."
    assert "fetched_at" in r
    assert backend.store["openserp_url_verdict:http://good"] == "Summary of the page."


def test_positive_rerun_served_from_cache_no_refetch(backend):
    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    gc = MagicMock()
    with (
        patch("src.utils.url_fetch.fetch") as fetch,
        patch.object(oe, "_verify_result", return_value=True) as verify,
    ):
        r = oe.process_positive_url(
            "http://good", "T", "snip", "ctx", gc, entity_key="person#01HB"
        )
    assert r["summary"] == "Cached summary."
    assert not fetch.called  # no re-fetch within retention
    # relevance is still asked for this entity (its own cached verdict, if any, answers it)
    assert verify.call_args.kwargs["entity_key"] == "person#01HB"


def test_a_cached_summary_is_not_given_to_an_entity_it_is_not_about(backend):
    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    with patch.object(oe, "_verify_result", return_value=False):
        r = oe.process_positive_url(
            "http://good", "T", "snip", "ctx", MagicMock(), entity_key="person#01HB"
        )
    assert r is None


def test_a_page_not_about_one_entity_is_not_rejected_for_every_entity(backend):
    gc = MagicMock()
    with patch.object(oe, "_verify_result", return_value=False):
        r = oe.process_positive_url("http://bad", "T", "snip", "ctx", gc)
    assert r is None
    assert "openserp_url_verdict:http://bad" not in backend.store  # not URL-wide


def test_rejected_rerun_skips_reprocessing(backend):
    backend.store["openserp_url_verdict:http://bad"] = "REJECT"
    gc = MagicMock()
    with patch.object(oe, "_verify_result") as verify:
        r = oe.process_positive_url("http://bad", "T", "snip", "ctx", gc)
    assert r is None
    assert not verify.called  # negative cache prevents reprocessing


def test_dead_page_after_verify_is_negative_cached(backend):
    gc = MagicMock()
    from src.utils import url_fetch

    dead = url_fetch.FetchResult(url="http://dead", source=url_fetch.DEAD)
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch("src.utils.url_fetch.fetch", return_value=dead),
    ):
        r = oe.process_positive_url("http://dead", "T", "snip", "ctx", gc)
    assert r is None  # verified but unfetchable -> no result
    assert backend.store["openserp_url_verdict:http://dead"] == "REJECT"


def test_skip_known_url_not_negative_cached(backend):
    """IMPORTANT regression: url_fetch SKIP (already searched -> reuse) must NOT be cached
    as REJECT — otherwise a second unmerged entity hitting the same URL is permanently
    denied the summary the URL actually has."""
    gc = MagicMock()
    from src.utils import url_fetch

    skip = url_fetch.FetchResult(url="http://seen", source=url_fetch.SKIP)
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch("src.utils.url_fetch.fetch", return_value=skip),
    ):
        r = oe.process_positive_url("http://seen", "T", "snip", "ctx", gc)
    assert r is None  # no summary THIS run
    assert (
        "openserp_url_verdict:http://seen" not in backend.store
    )  # NOT negative-cached


def test_deferred_url_not_negative_cached(backend):
    """DEFERRED (in-progress/cooldown -> retry later) must also NOT be cached as REJECT."""
    gc = MagicMock()
    from src.utils import url_fetch

    deferred = url_fetch.FetchResult(url="http://later", source=url_fetch.DEFERRED)
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch("src.utils.url_fetch.fetch", return_value=deferred),
    ):
        r = oe.process_positive_url("http://later", "T", "snip", "ctx", gc)
    assert r is None
    assert "openserp_url_verdict:http://later" not in backend.store


# --- relevance is per (URL, entity); the page is per URL (owner fix 2026-11-17) ------------------


class _TtlBackend(_Backend):
    def __init__(self):
        super().__init__()
        self.ttl = {}

    def put(self, k, v, ttl_days=30):
        self.store[k], self.ttl[k] = v, ttl_days


@pytest.fixture
def verdicts(monkeypatch):
    """Grok's relevance answers, counted; the verdict cache is a real (in-memory) one."""
    b = _TtlBackend()
    monkeypatch.setattr("src.utils.search_cache._get_backend", lambda: b)
    asked = []

    def ask(prompt, system, grok, cache_type, parse):
        asked.append(prompt)
        return "NO" if len(asked) == 1 else "YES"

    monkeypatch.setattr(oe, "_ask_verdicts", ask)
    return b, asked


def test_one_entitys_no_is_not_another_entitys_answer(verdicts):
    backend, asked = verdicts
    url = "https://example.org/unit-history"
    args = ("3rd Armored history", "ctx", object())
    assert oe._verify_result(*args, url=url, entity_key="people_group#01HA") is False
    assert oe._verify_result(*args, url=url, entity_key="people_group#01HB") is True
    # each entity's verdict is reused for that entity only: no third question
    assert oe._verify_result(*args, url=url, entity_key="people_group#01HA") is False
    assert len(asked) == 2


def test_urls_sharing_a_long_prefix_do_not_share_an_answer(verdicts):
    _, asked = verdicts
    stem = "https://example.org/" + "a" * 80
    oe._verify_result("t", "ctx", object(), url=stem + "/one")
    oe._verify_result("t", "ctx", object(), url=stem + "/two")
    assert len(asked) == 2


def test_a_verdict_is_kept_for_the_page_retention_window(verdicts):
    backend, _ = verdicts
    oe._verify_result(
        "t", "ctx", object(), url="https://example.org/x", entity_key="place#01H"
    )
    assert set(backend.ttl.values()) == {oe._URL_FETCH_RETENTION_DAYS}


def test_verdict_keys_are_unambiguous_and_carry_the_entity_and_its_context():
    # a "|" in a title cannot make two different results share a key
    assert oe._verify_key("c", "a|b", "u") != oe._verify_key("c", "a", "b|u")
    # the entity key ignores the result title, but not the entity's context
    assert oe._verify_key("c", "t1", "u", "person#01H") == oe._verify_key(
        "c", "t2", "u", "person#01H"
    )
    assert oe._verify_key("c", "t", "u", "person#01H") != oe._verify_key(
        "c (alias)", "t", "u", "person#01H"
    )
    assert oe._verify_key("c", "t", "u", "person#01H") != oe._verify_key("c", "t", "u")


def test_the_batch_verifier_uses_the_same_key(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "src.utils.search_cache.get_cached", lambda s, q: seen.append(q) or "YES"
    )
    results = [{"title": "t", "url": "https://e.org/x"}]
    assert oe._verify_results_batch("c", results, object(), entity_key="person#01H")
    assert seen == [oe._verify_key("c", "t", "https://e.org/x", "person#01H")]


def test_a_cached_summary_without_grok_is_not_handed_out(backend):
    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    assert oe.process_positive_url("http://good", "T", "s", "ctx", None) is None


def test_a_batch_queued_relevance_question_on_a_cached_page_writes_nothing(backend):
    from src.grok_client import BatchModeCollecting

    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    before = dict(backend.store)
    with patch.object(oe, "_ask_verdicts", side_effect=BatchModeCollecting("queued")):
        with pytest.raises(BatchModeCollecting):
            oe.process_positive_url(
                "http://good", "T", "s", "ctx", MagicMock(), entity_key="place#01H"
            )
    assert backend.store == before


def test_a_reused_page_is_counted(backend):
    oe.reset_metrics()
    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    with patch.object(oe, "_verify_result", return_value=True):
        oe.process_positive_url("http://good", "T", "s", "ctx", MagicMock())
    assert oe.get_metrics()["pages_reused"] == 1


def test_find_page_asks_relevance_for_its_own_entity(monkeypatch, queued_finds):
    from src.utils import find_queue

    seen = {}

    def proc(url, title, snippet, context, grok, entity_key=""):
        seen["entity_key"] = entity_key
        return None

    monkeypatch.setattr(oe, "process_positive_url", proc)
    origin = find_queue.entity_origin("place", {"PlaceID": "01HPLACE"})
    oe.find_page("https://example.org/metz", "Metz", "", "ctx", object(), origin)
    assert seen["entity_key"] == "place#01HPLACE"
