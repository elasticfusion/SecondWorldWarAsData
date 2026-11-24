"""Group 2 step 2b: under Phase 3 --batch a Grok call that misses the cache is QUEUED
(BatchModeCollecting). That is not an answer and not a failure: no handler may turn it into a
verdict (a rejection, a cached negative, a stamp, an attempt, an alert), and a loop over many items
must visit every one (so every request is queued in one pass) and raise ONCE at the end.
"""

import json
import logging
from pathlib import Path
from unittest import mock

import pytest

from src.grok_client import BatchModeCollecting
from src.utils.batch_pending import Pending


class Q:
    """A batch-mode Grok client: every request is queued."""

    batch_mode = True

    def _q(self, *a, **k):
        raise BatchModeCollecting("k")

    chat_completion = extract_json = extract_structured = _q
    extract_json_with_image = extract_json_with_image_base64 = _q


def _raise(*a, **k):
    raise BatchModeCollecting("k")


# --- the helper ---------------------------------------------------------------------------------


def test_pending_visits_every_item_and_raises_once():
    p = Pending()
    seen = []
    for i in range(3):
        with p.item():
            seen.append(i)
            _raise()
    assert seen == [0, 1, 2] and p.queued == 3 and p
    with pytest.raises(BatchModeCollecting):
        p.raise_if_queued()
    assert Pending().raise_if_queued() is None


def test_pending_lets_real_errors_through_and_call_returns_none_when_queued():
    p = Pending()
    with pytest.raises(ZeroDivisionError):
        with p.item():
            1 / 0
    assert p.call(_raise) is None and p.call(lambda x: x + 1, 1) == 2 and p.queued == 1


# --- bibliography ---------------------------------------------------------------------------------


def test_bibliography_verifiers_pass_a_queued_request_through(monkeypatch):
    from src.enrichment import bibliography_resolver as br

    with pytest.raises(BatchModeCollecting):
        br._verify_nara_match("q", "title", Q())
    with pytest.raises(BatchModeCollecting):
        br._verify_openserp_match("q", "title", "https://x.org/a", Q())
    with pytest.raises(BatchModeCollecting):
        br._verify_match("title", "a citation", Q())  # title-only branch: was ACCEPT
    monkeypatch.setattr(
        "src.utils.url_hygiene.fetch_text_safe", lambda *a, **k: ("page text", 200)
    )
    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: u)
    with pytest.raises(BatchModeCollecting):
        br._verify_match("title", "https://x.org/a", Q())  # the URL branch


def test_url_content_verifier_does_not_alert_or_reject_a_queued_request(
    monkeypatch, caplog
):
    from src.enrichment import bibliography_resolver as br

    monkeypatch.setattr(
        "src.utils.url_hygiene.fetch_text_safe", lambda *a, **k: ("page text", 200)
    )
    monkeypatch.setattr("src.utils.url_hygiene.clean_url", lambda u: u)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(BatchModeCollecting):
            br._verify_url_content("https://x.org/a", "citation", Q())
    assert "verify_url_error" not in caplog.text


def test_a_queued_nara_verify_is_not_a_genuine_not_found(monkeypatch):
    """(None, False) means "genuine not-found" and is cached as such: a queued verify must raise."""
    from src.enrichment import bibliography_resolver as br

    monkeypatch.setattr(br, "_extract_na_id", lambda hit: "123")
    monkeypatch.setattr(br, "_title_token_overlap", lambda a, b: True)
    monkeypatch.setattr(br, "_verify_nara_match", lambda *a, **k: _raise())
    hit = {"_source": {"record": {"title": "XIV Corps operations"}}}
    with pytest.raises(BatchModeCollecting):
        br._verify_nara_hits([hit], "XIV Corps", "XIV Corps ops", "q", Q())


def test_the_resolver_visits_every_record_then_raises_once(tmp_path, monkeypatch):
    from src.enrichment import bibliography_resolver as br

    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.json").write_text(json.dumps({"BibliographyID": n}))
    seen = []

    def proc(f, *a, **k):
        seen.append(f.name)
        _raise()

    monkeypatch.setattr(br, "_process_bib_file", proc)
    monkeypatch.setattr(br, "build_holdings_index", lambda: {})
    written = []
    monkeypatch.setattr(br, "_write_review_queue", lambda d, q: written.append(list(q)))
    with pytest.raises(BatchModeCollecting):
        br.resolve_bibliography_dir(tmp_path, Q(), {})
    assert seen == ["a.json", "b.json", "c.json"]  # every record queued its requests
    assert written and all(q == [] for q in written)  # nothing went to human review


def test_a_resolver_error_is_still_counted_for_a_real_failure(tmp_path, monkeypatch):
    from src.enrichment import bibliography_resolver as br

    (tmp_path / "a.json").write_text("{}")
    monkeypatch.setattr(br, "_process_bib_file", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(br, "build_holdings_index", lambda: {})
    monkeypatch.setattr(br, "_write_review_queue", lambda d, q: None)
    stats = br.resolve_bibliography_dir(tmp_path, Q(), {})
    assert stats["errors"] == 1


def test_the_bookseller_search_does_not_alert_on_a_queued_request(monkeypatch, caplog):
    from src.enrichment import bibliography_resolver as br

    monkeypatch.setattr(br, "_wants_bookseller", lambda *a: True)
    monkeypatch.setattr(br, "_find_bookseller", lambda *a: _raise())
    with pytest.raises(BatchModeCollecting):
        br._maybe_bookseller({}, "book", False, Q(), {})


def test_the_llm_search_and_its_verify_queue_instead_of_giving_up():
    from src.extraction import supplemental_search as ss

    with pytest.raises(BatchModeCollecting):
        ss.search_llm("Title", "Author", Q())


# --- supplemental_advanced (copyright / isbn / alt title) ----------------------------------------


def test_a_queued_death_date_is_not_an_unknown_copyright_status(tmp_path):
    from src.extraction import supplemental_advanced as sa

    with pytest.raises(BatchModeCollecting):
        sa.get_author_death_date("Some Author", Q())
    with pytest.raises(BatchModeCollecting):
        sa.extract_isbn({"title": "T", "author": "A", "year": 1990}, Q())
    with pytest.raises(BatchModeCollecting):
        sa._call_alt_title_expansion(Q(), "Interv w/ Gen X")


def test_a_record_with_queued_requests_is_not_stamped_and_its_siblings_still_queue(
    tmp_path, monkeypatch
):
    from src.extraction import supplemental_advanced as sa

    f = tmp_path / "b.json"
    f.write_text(json.dumps({"citation": {"title": "T"}}))
    calls = []
    monkeypatch.setattr(
        sa, "_enrich_alt_title", lambda *a: calls.append("alt") or _raise()
    )
    monkeypatch.setattr(sa, "_enrich_isbn", lambda *a: calls.append("isbn") or _raise())
    monkeypatch.setattr(
        sa, "_enrich_copyright", lambda *a: calls.append("copy") or _raise()
    )
    monkeypatch.setattr(sa, "_enrich_archive_url", lambda *a: False)
    with pytest.raises(BatchModeCollecting):
        sa._enrich_bib_file(f, {}, Q())
    assert calls == ["alt", "isbn", "copy"]  # all three queued in the same round
    assert "copyright_status" not in f.read_text()


def test_enrich_bibliography_visits_every_file_then_raises_once(tmp_path, monkeypatch):
    from src.extraction import supplemental_advanced as sa

    for n in "abc":
        (tmp_path / f"{n}.json").write_text("{}")
    seen = []
    monkeypatch.setattr(
        sa, "_enrich_bib_file", lambda f, c, g: seen.append(f.name) or _raise()
    )
    with pytest.raises(BatchModeCollecting):
        sa.enrich_bibliography(tmp_path, {}, Q())
    assert seen == ["a.json", "b.json", "c.json"]


# --- OpenSERP -------------------------------------------------------------------------------------


def test_a_queued_verify_is_not_a_failed_verify():
    from src.enrichment import openserp_enrichment as oe

    with pytest.raises(BatchModeCollecting):  # not VerifyFailed: no metric, no alert
        oe._ask_verdicts("p", "s", Q(), "openserp_verify", lambda r: None)


def test_a_queued_summary_does_not_stamp_the_url_or_cache_a_reject(monkeypatch):
    """The worst case: a REJECT cached for 90 days and the URL stamped searched, for a page that
    only had its summary queued."""
    from src.enrichment import openserp_enrichment as oe
    from src.utils import url_fetch, url_registry

    result = mock.Mock(
        source="live",
        content="<html>x</html>",
        url="https://x.org/p",  # the CLAIMED url (fetch claims the cleaned input)
        final_url="https://x.org/after-redirect",
    )
    monkeypatch.setattr(url_fetch, "fetch", lambda u: result)
    monkeypatch.setattr(oe, "_grok_summarize_html", lambda *a, **k: _raise())
    fin, rel = mock.Mock(), mock.Mock()
    monkeypatch.setattr(url_registry, "finalize_searched", fin)
    monkeypatch.setattr(url_registry, "release", rel)
    cached = mock.Mock()
    monkeypatch.setattr(oe, "_cache_url_verdict", cached)
    monkeypatch.setattr(oe, "_url_verdict_cached", lambda u: None)
    with pytest.raises(BatchModeCollecting):
        oe._summarize_url_page("https://x.org/p", "ctx", Q())
    fin.assert_not_called()
    rel.assert_called_once_with(
        "https://x.org/p"
    )  # the CLAIMED url, not the redirect target
    with pytest.raises(BatchModeCollecting):
        oe.process_positive_url("https://x.org/p", "t", "s", "ctx", Q())
    cached.assert_not_called()


def test_a_queued_unit_patch_does_not_stamp_the_group_searched(monkeypatch):
    from src.enrichment import openserp_enrichment as oe

    monkeypatch.setattr(oe, "_acquire_group_unit_patch", lambda *a, **k: _raise())
    with pytest.raises(BatchModeCollecting):
        oe._apply_group_unit_patch(
            {"group_name": "1st Infantry Division"},
            "1st Infantry Division",
            "US",
            "u",
            Q(),
            None,
        )


@pytest.mark.parametrize(
    "driver,iter_name",
    [
        ("enrich_groups_with_openserp", "_iter_loadable_records"),
        ("enrich_places_with_openserp", "_iter_loadable_records"),
    ],
)
def test_openserp_drivers_search_every_entity_then_raise_once(
    driver, iter_name, tmp_path, monkeypatch
):
    from src.enrichment import openserp_enrichment as oe

    recs = [
        (
            tmp_path / f"{i}.json",
            {
                "group_name": f"{i}th Infantry Division",
                "current_name": f"Place {i}",
                "name": f"Place {i}",
            },
        )
        for i in range(3)
    ]
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(oe, iter_name, lambda d: iter(recs))
    monkeypatch.setattr(oe, "place_name_variants", lambda d: [d["name"]])
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    searched, written = [], []
    monkeypatch.setattr(
        oe, "_search_entity", lambda *a, **k: searched.append(a[3]) or _raise()
    )
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: written.append(a))
    with pytest.raises(BatchModeCollecting):
        getattr(oe, driver)(tmp_path, "http://o", Q())
    assert len(searched) == 3 and written == []  # all visited, none stamped or written


def test_a_queued_license_check_does_not_reject_the_map(monkeypatch):
    from src.extraction import openserp_maps as om

    monkeypatch.setattr(om, "_is_trusted_domain", lambda d: False)
    monkeypatch.setattr(
        om, "_find_license_text", lambda *a, **k: ("t" * 200, "https://x.org/l")
    )
    monkeypatch.setattr(om, "_analyze_license_with_grok", lambda *a, **k: _raise())
    with pytest.raises(BatchModeCollecting):
        om._check_license_terms("https://x.org/m", Q())


def test_openserp_maps_visits_every_place_and_does_not_warn(
    tmp_path, monkeypatch, caplog
):
    from src.extraction import openserp_maps as om
    from src.extraction import search_history as sh

    pdir = tmp_path / "places"
    pdir.mkdir()
    for i in range(3):
        (pdir / f"{i}.json").write_text("{}")
    monkeypatch.setattr(sh.SearchHistory, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(
        sh.SearchHistory, "get_downloaded_urls", lambda self, d: set(), raising=False
    )
    monkeypatch.setattr(sh.SearchHistory, "searched_places", set(), raising=False)
    seen = []
    monkeypatch.setattr(
        om, "_process_place_file", lambda f, *a, **k: seen.append(f.name) or _raise()
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(BatchModeCollecting):
            om.import_openserp_maps(pdir, tmp_path / "out", Q())
    assert len(seen) == 3 and "Error processing" not in caplog.text


# --- people groups / places ---------------------------------------------------------------------


def test_the_enrichment_drain_visits_every_future_then_raises_once(caplog):
    from concurrent.futures import ThreadPoolExecutor

    from src.extraction._entity_common import drain_enrichment_futures

    def work(i):
        if i == 1:
            return True
        _raise()

    with ThreadPoolExecutor(2) as pool:
        futures = {pool.submit(work, i): Path(f"{i}.json") for i in range(4)}
        with caplog.at_level(logging.WARNING):
            with pytest.raises(BatchModeCollecting):
                drain_enrichment_futures(futures, 4, "group")
    assert "Failed to enrich" not in caplog.text  # a queued item is not a failed item


def test_a_queued_group_or_place_request_is_not_a_false_return_and_stamps_nothing(
    tmp_path,
):
    from src.extraction import enrich_groups as eg
    from src.extraction import enrich_places as ep

    g = tmp_path / "g.json"
    g.write_text(
        json.dumps({"group_name": "3rd Armored Division", "nationality": "US"})
    )
    before = g.read_text()
    with mock.patch.object(eg, "_maybe_source_recheck", lambda d, c: None):
        with pytest.raises(BatchModeCollecting):
            eg.enrich_group(g, Q())
    assert g.read_text() == before  # no not_found / enrichment stamp
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"current_name": "Sainte-Mere-Eglise"}))
    before = p.read_text()
    with pytest.raises(BatchModeCollecting):
        ep.enrich_place(p, Q())
    assert p.read_text() == before


def test_a_queued_source_recheck_is_not_skipped_with_a_warning(monkeypatch, caplog):
    from src.extraction import enrich_groups as eg

    monkeypatch.setattr(
        "src.extraction.group_source_recheck.recheck_group_from_source",
        lambda d, c: _raise(),
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(BatchModeCollecting):
            eg._maybe_source_recheck({"group_name": "x"}, Q())
    assert "source-recheck skipped" not in caplog.text


# --- geocoding ------------------------------------------------------------------------------------


def test_queued_places_are_not_errors_not_attempts_and_not_a_geocoder_outage(
    tmp_path, caplog
):
    from src.enrichment import places_grok_geocode as pg

    for i in range(8):  # more than GEOCODER_DOWN_STREAK
        (tmp_path / f"{i}.json").write_text(json.dumps({"current_name": f"Town {i}"}))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(BatchModeCollecting) as e:
            pg.geocode_places_dir(tmp_path, Q(), geocoder=lambda n, p, c: _raise())
    assert (
        "geocoder_unavailable" not in caplog.text
        and "Geocode failed" not in caplog.text
    )
    assert getattr(e.value, "geocoded", None) == 0
    # nothing was stamped: every file is unchanged and still needs geocoding
    assert all("geocode" not in (tmp_path / f"{i}.json").read_text() for i in range(8))


def test_the_geocode_report_counts_a_queued_place_only_as_queued(tmp_path):
    from src.enrichment import places_grok_geocode as pg

    f = tmp_path / "a.json"
    f.write_text(json.dumps({"current_name": "Town A"}))
    report = pg.GeocodeRunReport()
    pg._geocode_one_file(f, Q(), False, False, report, lambda n, p, c: _raise(), set())
    assert (report.attempted, report.errors, report.queued) == (0, 0, 1)
    assert report.details == []


# --- media, links, photos ---------------------------------------------------------------------


def test_in_doc_map_links_visit_every_link_and_raise_once(monkeypatch):
    from src.extraction import map_link_source as ml

    seen = []
    monkeypatch.setattr(
        ml, "_read_one_link", lambda m, *a: seen.append(m["url"]) or _raise()
    )
    docs = [
        {"book": "B", "maps": [{"url": f"https://x.org/{i}.png"} for i in range(3)]}
    ]
    with pytest.raises(BatchModeCollecting):
        ml.read_in_doc_maps(docs, Q(), Path("/nonexistent"), allowed_hosts=("x.org",))
    assert len(seen) == 3


def test_one_in_doc_map_link_does_not_warn_or_count_skipped_when_queued(
    monkeypatch, caplog, tmp_path
):
    from src.extraction import map_link_source as ml

    monkeypatch.setattr(ml, "_ssrf_safe_image_url", lambda u: u)
    monkeypatch.setattr(ml, "_host_allowed", lambda *a: True)
    monkeypatch.setattr(ml, "_vision_depicted", lambda *a, **k: _raise())
    summary = {"links": 0, "read": 0, "depicted": 0, "already": 0, "skipped": 0}
    rec = tmp_path / "m.json"
    rec.write_text('{"MapID": "01HX7YZABCDEFGHJKMNPQRSTVW"}')
    with pytest.raises(BatchModeCollecting):
        ml._read_one_link(
            {"url": "https://x.org/a.png"},
            ml._MapRead(Q(), {}, ("x.org",), 5),
            rec,
            "B",
            summary,
        )
    assert summary["skipped"] == 0


def test_a_queued_map_classification_still_saves_the_url_index(tmp_path, monkeypatch):
    """A Map created before its vision classification was queued must be in the saved URL index, or
    the next pass creates a second Map for the same URL."""
    from src.extraction import map_link_source as ml

    def one(m, book, maps_dir, url_index, *a, **k):
        url_index[m["url"]] = "MAPID" + m["url"][-5:]
        _raise()

    monkeypatch.setattr(ml, "_persist_one_map", one)
    docs = [
        {
            "book": "B",
            "maps": [{"url": "https://x.org/1.png"}, {"url": "https://x.org/2.png"}],
        }
    ]
    with pytest.raises(BatchModeCollecting):
        ml.persist_map_entities(docs, tmp_path / "maps", grok_client=Q())
    saved = json.loads((tmp_path / "maps" / "index.json").read_text())
    assert len(saved) == 2


def test_deferred_image_vision_queues_every_image_without_an_undecided_alert(
    tmp_path, monkeypatch, caplog
):
    from src.extraction import image_map_vision as imv

    idir = tmp_path / "images"
    idir.mkdir()
    for i in range(3):
        (idir / f"{i}.json").write_text("{}")
    seen = []
    monkeypatch.setattr(
        imv, "_resolve_file", lambda f, *a: seen.append(f.name) or _raise()
    )
    alerts = []
    monkeypatch.setattr(imv, "observe_warn", lambda *a, **k: alerts.append(a))
    with pytest.raises(BatchModeCollecting):
        imv.resolve_pending(tmp_path, Q())
    assert len(seen) == 3 and alerts == []  # no map_vision_incomplete


def test_a_queued_link_relevancy_is_neither_enqueued_nor_dropped(monkeypatch):
    from src.extraction import link_assets as la
    from src.extraction import link_relevancy as lr

    with pytest.raises(BatchModeCollecting):
        lr.grok_link_verdict(Q(), "text", "https://x.org/a", "book")
    with pytest.raises(BatchModeCollecting):
        lr.should_enqueue_link("https://x.org/a", "text", grok_client=Q())
    enq = []
    monkeypatch.setattr(la, "_enqueue_one", lambda *a, **k: enq.append(a))
    monkeypatch.setattr(la, "_safe_load_blacklist", lambda f: [])
    content = "[a](https://x.org/1) [b](https://x.org/2) [c](https://x.org/3)"
    with pytest.raises(BatchModeCollecting):
        la.enqueue_doc_links(content, "https://base.org/", "B", grok_client=Q())
    assert enq == []  # nothing queued for human review on a verdict nobody made


def test_a_queued_terrain_verdict_is_not_a_skip(monkeypatch):
    from src.extraction import image_place_link as ip

    with pytest.raises(BatchModeCollecting):
        ip._grok_is_terrain("Aerial view of the beach", Q())
    seen = []
    monkeypatch.setattr(
        ip, "_link_one_image", lambda cap, *a: seen.append(cap) or _raise()
    )
    monkeypatch.setattr(ip, "_build_book_section_index", lambda d: {})
    monkeypatch.setattr(ip, "_safe_load_index", lambda *a: {})
    monkeypatch.setattr(
        "src.extraction.places._build_place_name_index", lambda d: ({}, {})
    )
    docs = [
        {
            "book": "B",
            "images": [
                {"caption": f"c{i}", "url": f"https://x.org/{i}.jpg"} for i in range(3)
            ],
        }
    ]
    with pytest.raises(BatchModeCollecting):
        ip.link_images_to_places(docs, Path("/tmp/none/places"), grok_client=Q())
    assert seen == ["c0", "c1", "c2"]


def test_queued_entities_count_against_max_items(tmp_path, monkeypatch):
    """A batch round must not queue more paid requests than --max-items allows."""
    from src.enrichment import openserp_enrichment as oe

    recs = [
        (tmp_path / f"{i}.json", {"group_name": f"{i}th Division"}) for i in range(10)
    ]
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(oe, "_iter_loadable_records", lambda d: iter(recs))
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    searched = []
    monkeypatch.setattr(
        oe, "_search_entity", lambda *a, **k: searched.append(a[3]) or _raise()
    )
    with pytest.raises(BatchModeCollecting):
        oe.enrich_groups_with_openserp(tmp_path, "http://o", Q(), max_items=4)
    assert len(searched) == 4


def test_in_doc_map_read_still_runs_when_the_persist_queued(monkeypatch):
    """Both queue their requests in the same round (one batch wait, not two); the Map records are
    persisted first, so the read has a record to store its answer on."""
    import phase3_enrich_data as P
    from src.extraction import map_link_source as ml

    calls = []
    monkeypatch.setattr(P, "_load_parsed_docs", lambda out: [{"book": "B"}])
    monkeypatch.setattr(P, "_recycle_in_doc_maps", lambda cfg: True)
    monkeypatch.setattr(
        ml,
        "read_in_doc_maps",
        lambda *a, **k: calls.append("read") or _raise(),
    )
    monkeypatch.setattr(
        ml, "persist_map_entities", lambda *a, **k: calls.append("persist") or _raise()
    )
    with pytest.raises(BatchModeCollecting):
        P._ingest_in_doc_map_links(Path("/tmp/x"), Q(), {})
    assert calls == ["persist", "read"]


# --- registry ------------------------------------------------------------------------------------


def test_releasing_a_url_claim_only_deletes_an_in_progress_row(monkeypatch):
    import boto3
    from moto import mock_aws

    from src.utils import url_registry as ur

    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="t",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table = boto3.resource("dynamodb", region_name="us-east-1").Table("t")
        monkeypatch.setattr(ur, "_table", lambda: table)
        url = "https://example.org/page"
        assert ur.claim(url)
        assert ur.release(url) is True
        assert ur.claim(url)  # free again
        ur.finalize_searched(url, relevant=True, context="c", source="openserp")
        assert ur.release(url) is False  # a searched record is never touched
        assert ur.decide(url)[0] == ur.SKIP_KNOWN
