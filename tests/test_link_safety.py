"""Known-malicious links (owner 2026-11-24): Google Safe Browsing (free tier) finds them; they are
not fetched, not queued, removed from kept pages, recorded and alerted once; an operator can clear
a false positive; with no key nothing is checked (and that is said)."""

import json
import logging

import pytest

from src.utils import find_pages, find_queue, link_safety, url_fetch

BAD = "https://malware.example.com/payload"
GOOD = "https://www.ibiblio.org/hyperwar/"


class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def sb(monkeypatch):
    """Safe Browsing with BAD listed; counts requests; collects alerts."""
    asked, alerts, listed = [], [], {BAD: "MALWARE"}

    def post(url, json=None, headers=None, timeout=None):
        assert headers["X-Goog-Api-Key"] == "k" and "key=" not in url
        urls = [e["url"] for e in json["threatInfo"]["threatEntries"]]
        asked.append(urls)
        return _Resp(
            {
                "matches": [
                    {"threatType": listed[u], "threat": {"url": u}}
                    for u in urls
                    if u in listed
                ]
            }
        )

    monkeypatch.setenv(link_safety.KEY_ENV, "k")
    monkeypatch.setattr(
        link_safety, "_config", lambda: {}
    )  # on (conftest turns it off)
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        "src.utils.observe.publish_alert", lambda subj, msg: alerts.append(msg) or True
    )
    return asked, alerts, listed


def test_a_listed_url_is_flagged_recorded_and_alerted_once(sb):
    asked, alerts, _ = sb
    assert link_safety.is_flagged(BAD, seen_in="test")
    assert link_safety.is_flagged(
        BAD
    )  # from the record: not asked again, not alerted again
    assert len(asked) == 1 and len(alerts) == 1
    assert "allow_link.py" in alerts[0] and "Advisory provided by Google" in alerts[0]
    rec = link_safety.flagged_record(BAD)
    assert rec["threats"] == ["MALWARE"] and rec["seen_in"] == ["test"]
    assert rec["provider"] == link_safety.PROVIDER


def test_a_clean_url_is_asked_about_once(sb):
    asked, _, _ = sb
    assert not link_safety.is_flagged(GOOD)
    assert not link_safety.is_flagged(GOOD)
    assert len(asked) == 1


def test_urls_are_checked_in_one_batched_request(sb):
    asked, _, _ = sb
    urls = [f"https://site{n}.example.org/" for n in range(700)] + [BAD]
    bad = link_safety.flagged(urls)
    assert list(bad) == [BAD]
    assert [len(a) for a in asked] == [500, 201]


def test_no_answer_blocks_nothing_and_caches_nothing(sb, monkeypatch, caplog):
    from src.utils import observe
    from src.utils.search_cache import get_cached

    observe._counts.clear()
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({}, status=503))
    with caplog.at_level(logging.WARNING):
        assert link_safety.flagged([BAD]) == {}
    assert "OBSERV link_safety.unavailable" in caplog.text
    assert (
        get_cached(link_safety._CLEAN_SOURCE, BAD) is None
    )  # nothing unchecked is kept
    assert link_safety.flagged_record(BAD) is None


def test_after_no_answer_the_api_is_left_alone_for_a_while(sb, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "requests.post", lambda *a, **k: calls.append(1) or _Resp({}, status=500)
    )
    link_safety.flagged(["https://a.example.org/"])
    link_safety.flagged(["https://b.example.org/"])
    assert (
        len(calls) == 1
    )  # the second waits out the breaker (not another 10 s timeout)


def test_the_key_never_appears_in_a_log(sb, monkeypatch, caplog):
    def boom(*a, **k):
        raise ConnectionError("https://safebrowsing.googleapis.com/?key=k failed")

    from src.utils import observe

    observe._counts.clear()
    monkeypatch.setattr("requests.post", boom)
    with caplog.at_level(logging.DEBUG):
        link_safety.flagged([BAD])
    assert "key=k" not in caplog.text and "ConnectionError" in caplog.text


def test_without_a_key_nothing_is_checked_and_that_is_said(monkeypatch, caplog):
    from src.utils import observe

    observe._counts.clear()
    monkeypatch.delenv(link_safety.KEY_ENV, raising=False)
    monkeypatch.setattr(link_safety, "_config", lambda: {})
    monkeypatch.setattr("requests.post", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.WARNING):
        assert link_safety.flagged([BAD]) == {}
    assert "OBSERV link_safety.off_no_key" in caplog.text


def test_switched_off_says_nothing(monkeypatch, caplog):
    from src.utils import observe

    observe._counts.clear()
    monkeypatch.setenv(link_safety.KEY_ENV, "k")
    monkeypatch.setattr(link_safety, "_config", lambda: {"enabled": False})
    monkeypatch.setattr("requests.post", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.WARNING):
        assert link_safety.flagged([BAD]) == {}
    assert "link_safety" not in caplog.text


def test_an_operator_can_clear_a_false_positive(sb):
    asked, alerts, _ = sb
    assert link_safety.is_flagged(BAD)
    assert link_safety.allow(BAD, "checked by hand")
    assert not link_safety.is_flagged(BAD)
    assert len(alerts) == 1  # the allowed URL is not alerted again


def test_the_allow_script(sb, capsys):
    from scripts.allow_link import main

    link_safety.is_flagged(BAD)
    assert main(["--url", BAD, "--show"]) == 0 and "MALWARE" in capsys.readouterr().out
    assert main(["--url", BAD]) == 2  # a reason is required
    assert main(["--url", BAD, "--reason", "false positive"]) == 0
    assert link_safety.flagged_record(BAD) is None


def test_a_flagged_url_is_not_fetched(sb, monkeypatch):
    monkeypatch.setattr(url_fetch, "_do_fetch", lambda *a: 1 / 0)
    got = url_fetch.fetch(BAD)
    assert got.source == url_fetch.SKIP and "malicious" in got.detail


def test_no_pinned_get_reaches_a_flagged_url(sb, monkeypatch):
    """safe_get, every vet_url_for_fetch caller and each url_fetch redirect hop pin through
    _pinned_get_once: it refuses before any connection."""
    import requests

    from src.utils import url_hygiene

    monkeypatch.setattr(
        url_hygiene, "resolved_host_is_safe", lambda h: ["93.184.216.34"]
    )
    monkeypatch.setattr(requests.Session, "send", lambda *a, **k: 1 / 0)
    assert url_hygiene.safe_get(BAD) is None
    with pytest.raises(link_safety.LinkFlagged):
        url_hygiene._pinned_get(
            BAD, "malware.example.com", ["93.184.216.34"], timeout=5, headers={}
        )


def test_a_listed_url_with_a_query_is_asked_about_with_it(sb):
    asked, _, listed = sb
    listed["https://x.example.org/p?id=evil"] = "SOCIAL_ENGINEERING"
    assert link_safety.is_flagged("https://X.example.org/p?id=evil#frag")
    assert asked[-1] == ["https://x.example.org/p?id=evil"]


def test_the_alert_link_is_not_clickable(sb):
    _, alerts, _ = sb
    link_safety.is_flagged(BAD)
    assert (
        BAD not in alerts[0] and "hxxps://malware[.]example[.]com/payload" in alerts[0]
    )


def test_a_flagged_image_is_not_downloaded(sb, monkeypatch):
    from src.extraction import images

    sb[2][BAD + ".png"] = "MALWARE"
    monkeypatch.setattr(images, "_safe_image_response_with_reason", lambda c: 1 / 0)
    assert images.fetch_image_bytes_with_reason(BAD + ".png") == (None, None, "flagged")


def test_a_flagged_find_never_reaches_a_human(sb, queued_finds):
    origin = find_queue.Origin(entity_type="person", entity_id="01HPERSON")
    status = find_queue.enqueue_find(
        provider="openserp-find",
        url=BAD,
        verdict=find_queue.RELEVANT,
        origin=origin,
        summary="s",
    )
    assert status == find_queue.REFUSED and queued_finds() == []


def test_flagged_search_results_are_dropped_before_any_grok_request(sb):
    import src.enrichment.openserp_enrichment as oe

    asked, _, _ = sb
    results = [
        {"url": GOOD, "title": "ok"},
        {"url": BAD, "title": "bad"},
        {
            "url": "https://img.example.org/a.jpg",
            "page_url": BAD,
        },  # an image on a bad page
    ]
    oe.reset_metrics()
    assert oe._drop_flagged(results) == [results[0]]
    assert len(asked) == 1  # one request for the whole result set
    assert oe.get_metrics()["results_flagged"] == 2
    oe.reset_metrics()


def test_a_kept_page_loses_its_flagged_links_but_keeps_their_text(sb):
    html = (
        f'<html><body><p>See <a href="{BAD}">the payload</a> and '
        f'<a href="{GOOD}">HyperWar</a>.</p><img src="{BAD}" alt="x"></body></html>'
    )
    page = find_pages.keep("https://example.org/p", html)
    body = find_pages.load(page).decode()
    assert BAD not in body and GOOD in body and "the payload" in body
    assert page["page_links_removed"] == 2


@pytest.mark.parametrize(
    "href",
    ["//malware.example.com/payload", " https://malware.example.com/payload",
     "HTTPS://MALWARE.example.com/payload", "/payload"],  # fmt: skip
)
def test_a_link_written_another_way_is_still_removed(sb, href):
    page = f'<p><a href="{href}">x</a></p>'
    out, n = link_safety.strip_flagged_links(
        page, page_url="https://malware.example.com/p"
    )
    assert n == 1 and "href" not in out and ">x<" in out


def test_a_page_with_too_many_links_checks_the_first_and_says_so(
    sb, caplog, monkeypatch
):
    from src.utils import observe

    observe._counts.clear()
    monkeypatch.setattr(link_safety, "_PAGE_MAX", 3)
    page = "".join(f'<a href="https://s{n}.example.org/">x</a>' for n in range(5))
    with caplog.at_level(logging.WARNING):
        link_safety.strip_flagged_links(page, page_url="https://example.org/")
    assert "page_links_unchecked" in caplog.text and len(sb[0][-1]) == 3


def test_with_the_check_off_a_kept_page_is_as_before():
    page = find_pages.keep("https://example.org/p", f'<p><a href="{BAD}">x</a></p>')
    assert "page_links_removed" not in page and BAD in find_pages.load(page).decode()


def test_a_page_with_nothing_flagged_is_unchanged(sb):
    page = '<p><a href="https://a.example.org/x?a=1&amp;b=2">x</a></p>'
    assert link_safety.strip_flagged_links(page) == (page, 0)


def test_ecs_loads_the_key_from_its_secret(monkeypatch):
    from unittest import mock

    monkeypatch.setenv("S3_BUCKET", "test-bucket")  # ecs_entrypoint reads it on import
    import ecs_entrypoint as ee

    for name in ("GROK_API_KEY", "NARA_API_KEY"):
        monkeypatch.setenv(name, "set")
    monkeypatch.setenv("ENV_NAME", "dev")
    monkeypatch.setenv(
        link_safety.KEY_ENV, "x"
    )  # recorded, so the loaded key is undone after
    monkeypatch.delenv(link_safety.KEY_ENV)
    monkeypatch.setattr(ee, "_current_phase_script", "phase1_parse.py")
    sm = mock.Mock()
    sm.get_secret_value.return_value = {"SecretString": " sb-key \n"}
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: sm)
    ee._load_secrets()
    sm.get_secret_value.assert_called_once_with(
        SecretId="dev-wwii-pipeline/safe-browsing-api-key"
    )
    import os

    assert os.environ[link_safety.KEY_ENV] == "sb-key"


def test_the_flag_record_is_json_with_provenance(sb):
    link_safety.is_flagged(BAD, seen_in="page https://example.org/p")
    from src.utils.search_cache import get_cached

    rec = json.loads(get_cached(link_safety._FLAGGED_SOURCE, BAD))
    assert set(rec) >= {
        "url",
        "threats",
        "provider",
        "advisory",
        "flagged_at",
        "seen_in",
    }


@pytest.mark.parametrize(
    "href",
    [r"/\malware.example.com/payload", r"\\malware.example.com/payload",
     r"https:\\malware.example.com/payload", "http:malware.example.com/payload",
     "https:/malware.example.com/payload"],  # fmt: skip
)
def test_a_link_a_browser_reads_differently_is_still_removed(sb, href):
    """Browsers read ``\\`` as ``/`` and ``https:host`` as ``https://host``."""
    sb[2]["http://malware.example.com/payload"] = "MALWARE"
    page = f'<p><a href="{href}">x</a></p>'
    out, n = link_safety.strip_flagged_links(
        page, page_url="https://site.example.org/p/q"
    )
    assert n == 1 and "href" not in out and ">x<" in out


def test_odd_input_never_raises(sb):
    assert link_safety.flagged([None, 5, "http://[::1", "http://a:99999/"]) == {}
    assert link_safety._breaker["until"] == 0.0  # odd input is not an outage


@pytest.mark.parametrize(
    "href",
    ["https://good@malware.example.com/payload", "http:\t//malware.example.com/payload",
     "http:/\t/malware.example.com/payload", "\x01http:malware.example.com/payload",
     "https://malw\u00adare.example.com/payload"],  # fmt: skip
)
def test_a_link_that_cannot_be_read_safely_is_removed_not_kept(sb, href):
    """A web link read differently by a browser than by us is never kept unchecked."""
    sb[2]["http://malware.example.com/payload"] = "MALWARE"
    page = f'<p><a href="{href}">x</a></p>'
    out, n = link_safety.strip_flagged_links(
        page, page_url="https://site.example.org/p/q"
    )
    assert n == 1 and "href" not in out and ">x<" in out


def test_ordinary_links_are_kept(sb):
    page = (
        '<p><a href="/next">n</a><a href="#top">t</a><a href="mailto:a@b.org">m</a>'
        '<a href="https://www.ibiblio.org/hyperwar/">h</a></p>'
    )
    assert link_safety.strip_flagged_links(
        page, page_url="https://site.example.org/p"
    ) == (
        page,
        0,
    )


@pytest.mark.parametrize(
    "href",
    ["http://malware.example.com./payload", "http://malware%2eexample.com/payload",
     "http://%6dalware.example.com/payload", "http://malware\u3002example.com/payload",
     "http://malware\uff0eexample.com/payload", "http://malware\uff61example.com/payload",
     "http://\uff4dalware.example.com/payload"],  # fmt: skip
)
def test_another_spelling_of_a_flagged_host_is_removed(sb, href):
    """Browsers percent-decode and IDNA-map the host: so does the check."""
    sb[2]["http://malware.example.com/payload"] = "MALWARE"
    page = f'<p><a href="{href}">x</a></p>'
    out, n = link_safety.strip_flagged_links(
        page, page_url="https://site.example.org/p"
    )
    assert n == 1 and "href" not in out


def test_an_international_host_is_asked_about_in_its_ascii_form(sb):
    asked, _, _ = sb
    link_safety.is_flagged("https://bücher.de/x")
    assert asked[-1] == ["https://xn--bcher-kva.de/x"]


@pytest.mark.parametrize(
    "href",
    ["http://malware.example.com\u3002/payload", "http://malware.example.com\uff0e/payload",
     "http://malware.example.com%ef%bd%a1/payload", "http://malware.example.com%e3%80%82/payload",
     "http://malware.example.com:80/payload", "http://malware.example.com:0080/payload"],  # fmt: skip
)
def test_a_trailing_wide_dot_or_default_port_is_the_same_host(sb, href):
    sb[2]["http://malware.example.com/payload"] = "MALWARE"
    out, n = link_safety.strip_flagged_links(
        f'<p><a href="{href}">x</a></p>', page_url="https://site.example.org/p"
    )
    assert n == 1 and "href" not in out


def test_an_ipv6_link_keeps_its_brackets(sb):
    assert (
        link_safety.normalize("http://[2606:4700::1111]:8080/x")
        == "http://[2606:4700::1111]:8080/x"
    )
    assert link_safety.normalize("https://[2606:4700::1111]:443/x") == (
        "https://[2606:4700::1111]/x"
    )


@pytest.mark.parametrize(
    "url", ["http://evil.com%2fz/", "http://evil.com%3a80/x", "http://evil.com%00/x"]
)
def test_a_host_that_decodes_into_url_structure_is_not_a_url(url):
    assert link_safety.normalize(url) is None


@pytest.mark.parametrize(
    "href",
    ["https:///malware.example.com/payload", "https:////malware.example.com/payload",
     r"https:/\malware.example.com/payload"],  # fmt: skip
)
def test_extra_slashes_after_the_scheme_are_skipped_as_a_browser_does(sb, href):
    sb[2]["https://malware.example.com/payload"] = "MALWARE"
    out, n = link_safety.strip_flagged_links(
        f'<p><a href="{href}">x</a></p>', page_url="https://site.example.org/p"
    )
    assert n == 1 and "href" not in out


@pytest.mark.parametrize(
    "url,want",
    [("https://16909060/x", "https://1.2.3.4/x"), ("https://0x1.2.3.4/x", "https://1.2.3.4/x"),
     ("https://01.2.3.4/x", "https://1.2.3.4/x"), ("https://cafe.ab/x", "https://cafe.ab/x"),
     ("https://[2606:4700:0:0::1111]/x", "https://[2606:4700::1111]/x")],  # fmt: skip
)
def test_ip_addresses_are_asked_about_in_one_spelling(url, want):
    assert link_safety.normalize(url) == want


@pytest.mark.parametrize(
    "href",
    ["///malware.example.com/payload", "////malware.example.com/payload",
     r"\\\malware.example.com/payload"],  # fmt: skip
)
def test_extra_leading_slashes_on_a_scheme_relative_link_are_skipped(sb, href):
    sb[2]["https://malware.example.com/payload"] = "MALWARE"
    out, n = link_safety.strip_flagged_links(
        f'<p><a href="{href}">x</a></p>', page_url="https://site.example.org/p"
    )
    assert n == 1 and "href" not in out


def test_an_allow_covers_every_spelling_of_the_url(sb):
    sb[2]["http://example.com/a?x=1"] = "MALWARE"
    assert link_safety.is_flagged("http://EXAMPLE.com:80/a?x=1")
    assert link_safety.allow("http://example.com/a?x=1#f", "checked")
    assert link_safety.flagged_record("http://EXAMPLE.com:80/a?x=1") is None


@pytest.mark.parametrize(
    "url",
    ["https://bücher.de/x?a=1", "http://[2606:4700::1111]:8080/x", "https://16909060/x",
     "https://malware.example.com./p", "HTTPS://Site.Example.org:443/a#f", "https://my_host.example.org/"],  # fmt: skip
)
def test_normalizing_twice_changes_nothing(url):
    once = link_safety.normalize(url)
    assert once and link_safety.normalize(once) == once
