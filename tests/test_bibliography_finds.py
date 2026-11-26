"""bibliography_resolve's finds go to the human review queue (Group 3, owner 2026-11-17/21).

A verified copy is queued (tied to the BibliographyID), the record waits as ``pending``, an approved
find becomes its ``resource_urls``, a rejected one is never shown again, and a NARA find carries
what a download would fetch. Nothing is downloaded by the resolver: a human starts it.
"""

import json
from unittest.mock import patch

import pytest

import src.enrichment.bibliography_resolver as br
from src.enrichment import bibliography_finds as bf
from src.enrichment import fetch_review
from src.utils import find_queue

BIB = "01HX7YZABCDEFGHJKMNPQRSTVW"
ITEM = "https://archive.org/details/lorrainecampaign00cole"
NARA = "https://catalog.archives.gov/id/305271"


def _entry(**kw):
    return {
        "BibliographyID": BIB,
        "title": "The Lorraine Campaign",
        "citation": {"title": "The Lorraine Campaign", "author": ["Cole"]},
        "verbatim_reference": "Cole, The Lorraine Campaign",
        **kw,
    }


def _queue(entry, findings, details=None, config=None):
    return bf.queue_findings(
        entry,
        findings,
        query="Cole, The Lorraine Campaign",
        grok_client=object(),
        config=config or {},
        details=details,
    )


def test_a_verified_copy_is_queued_and_the_record_waits(queued_finds):
    entry = _entry()
    br._apply_result(entry, (ITEM, "archive_org", {"pdf_url": ITEM + ".pdf"}), object())
    assert entry["search_status"] == "pending"
    assert "resource_urls" not in entry  # nothing is written until a human approves it
    (e,) = queued_finds("archive-org-find")
    p = e["request_params"]
    assert e["endpoint"] == ITEM and e["review_status"] == "pending"
    assert (p["origin_entity_type"], p["origin_entity_id"]) == ("bibliography", BIB)
    assert p["BibliographyID"] == BIB and p["search_source"] == "archive_org"
    assert p["query"] == "Cole, The Lorraine Campaign"
    assert p["source_metadata"] == {
        "pdf_url": ITEM + ".pdf"
    }  # applied only on approval


def test_record_group_facts_go_on_the_record_at_once(queued_finds):
    entry = _entry()
    extra = {"archive_reference_number": "RG 407", "archive_physical_address": "NARA"}
    br._apply_result(entry, (None, "nara_grok", extra), object())
    assert entry["archive_reference_number"] == "RG 407"
    assert entry["search_status"] == "reference_identified" and queued_finds() == []


def test_an_api_address_is_never_queued(queued_finds, caplog):
    for url in (
        "https://archive.org/advancedsearch.php?q=x",
        "https://archive.org/metadata/x",
        "https://catalog.archives.gov/api/v2/records/search?q=x",
    ):
        got = _queue(_entry(), [(url, "archive_org")])
        assert got.refused == 1 and got.live == 0
    assert queued_finds("archive-org-find") == []
    assert "OBSERV bib_finds.not_a_find" in caplog.text


def test_without_grok_nothing_is_queued(queued_finds):
    got = bf.queue_findings(
        _entry(), [(ITEM, "archive_org")], query="q", grok_client=None, config={}
    )
    assert got.refused == 1 and queued_finds("archive-org-find") == []


def test_a_nara_find_carries_what_a_download_would_fetch(queued_finds):
    objs = [f"https://catalog.archives.gov/medialz/x/{i:04d}.tif" for i in range(30)]
    with patch(
        "src.enrichment.acquisition_fetchers.nara_digital_objects",
        return_value={"title": "XIV Corps: 4-004", "object_urls": objs},
    ) as look:
        _queue(_entry(), [(NARA, "nara_catalog")], config={"nara_api_key": "k"})
    look.assert_called_once_with("305271", "k")
    (e,) = queued_finds("nara-find")
    d = e["request_params"]["download"]
    assert d["naId"] == "305271" and d["object_count"] == 30
    assert (
        d["formats"] == ["tif"] and len(d["object_urls"]) == 20
    )  # a sample; all are re-read


def test_a_nara_lookup_that_fails_never_says_no_scans(queued_finds):
    with patch(
        "src.enrichment.acquisition_fetchers.nara_digital_objects", return_value=None
    ):
        _queue(_entry(), [(NARA, "nara_catalog")], config={"nara_api_key": "k"})
    (e,) = queued_finds("nara-find")
    assert e["request_params"]["download"]["object_count"] is None  # unknown, not 0


def _record(tmp_path, **kw):
    f = tmp_path / "bib_x.json"
    f.write_text(json.dumps(_entry(**kw)))
    return f


def _run(tmp_path, config=None):
    cfg = {"holdings_index": {}, "use_openserp": False, **(config or {})}
    return br.resolve_bibliography_dir(tmp_path, object(), cfg)


def _search(urls):
    return patch.object(
        br,
        "_search_online_sources",
        # the real call: (entry, verbatim, search_text, title, grok_client, config)
        side_effect=lambda entry, *a: (
            br._queue_found(entry, urls, "q", a[3], a[4]) or urls[0]
        ),
    )


def test_a_waiting_record_is_not_searched_again(tmp_path, queued_finds):
    f = _record(tmp_path)
    with _search([(ITEM, "archive_org")]) as search:
        _run(tmp_path)
        assert json.loads(f.read_text())["search_status"] == "pending"
        stats = _run(tmp_path)
    assert search.call_count == 1 and stats["waiting"] == 1
    assert len(queued_finds("archive-org-find")) == 1


def test_an_approved_find_becomes_the_records_copy(tmp_path, queued_finds):
    f = _record(tmp_path)
    with _search([(ITEM, "archive_org")]):
        _run(tmp_path)
    (e,) = queued_finds("archive-org-find")
    fetch_review.set_review_status(e["fetch_event_id"], "affirmed")
    with _search([(ITEM, "archive_org")]) as search:
        _run(tmp_path)
    rec = json.loads(f.read_text())
    assert search.call_count == 0  # resolved by the approval, nothing searched
    assert rec["resource_urls"] == [ITEM] and rec["search_status"] == "resolved"
    assert rec["search_source"] == "archive_org" and rec["availability"] == "online"
    (finding,) = rec["source_metadata"]["findings"]
    assert (
        finding["fetch_event_id"] == e["fetch_event_id"]
    )  # provenance of the approval
    assert finding["verified_by"] == "grok+human"


def test_a_rejected_find_is_searched_again_but_never_shown_twice(
    tmp_path, queued_finds
):
    f = _record(tmp_path)
    with _search([(ITEM, "archive_org")]):
        _run(tmp_path)
    (e,) = queued_finds("archive-org-find")
    assert fetch_review.reject_fetch_find(e)
    with _search([(ITEM, "archive_org")]) as search:
        _run(tmp_path)
    rec = json.loads(f.read_text())
    assert search.call_count == 1  # nothing left waiting: searched again
    assert rec["search_status"] == "not_found" and "resource_urls" not in rec
    assert (
        len(queued_finds("archive-org-find")) == 1
    )  # the rejected URL is not queued again


def test_an_unreadable_queue_neither_reconciles_nor_searches(tmp_path, monkeypatch):
    f = _record(tmp_path, search_status="pending")
    monkeypatch.setattr(fetch_review, "load_events", lambda: ([], 1))
    with _search([(ITEM, "archive_org")]) as search:
        _run(tmp_path)
    assert search.call_count == 0
    assert json.loads(f.read_text())["search_status"] == "pending"


def test_a_pending_record_with_nothing_waiting_is_searched_again(tmp_path):
    """e.g. its finds were queued under an identical citation: it queues its own."""
    f = _record(tmp_path, search_status="pending")
    with _search([(ITEM, "archive_org")]) as search:
        _run(tmp_path)
    assert search.call_count == 1
    assert json.loads(f.read_text())["search_status"] == "pending"


def test_an_identical_citation_queues_its_own_finds(tmp_path, queued_finds):
    for n, bid in enumerate(
        ("01HX7YZABCDEFGHJKMNPQRSTVA", "01HX7YZABCDEFGHJKMNPQRSTVB")
    ):
        (tmp_path / f"bib_{n}.json").write_text(json.dumps(_entry(BibliographyID=bid)))
    with _search([(ITEM, "archive_org")]):
        _run(tmp_path)
    ids = {
        e["request_params"]["origin_entity_id"]
        for e in queued_finds("archive-org-find")
    }
    assert ids == {"01HX7YZABCDEFGHJKMNPQRSTVA", "01HX7YZABCDEFGHJKMNPQRSTVB"}


def test_a_find_the_queue_could_not_store_is_an_outage(tmp_path, monkeypatch):
    monkeypatch.setattr(find_queue, "enqueue_find", lambda **k: find_queue.FAILED)
    f = _record(tmp_path)
    with _search([(ITEM, "archive_org")]):
        stats = _run(tmp_path)
    assert (
        stats["backend_down"] == 1 and stats["queued"] == 0
    )  # retried, not a human item
    assert "resource_urls" not in json.loads(f.read_text())


def test_a_waiting_record_is_not_a_not_found_review_item(tmp_path):
    _record(tmp_path)
    with _search([(ITEM, "archive_org")]):
        stats = _run(tmp_path)
    assert stats["pending"] == 1 and stats["queued"] == 0
    assert not (tmp_path / "review_queue.json").exists()


@pytest.mark.parametrize(
    "source,provider",
    sorted(bf.SOURCE_PROVIDER.items()),
)
def test_every_resolver_source_has_a_find_provider(source, provider):
    from src.utils import fetch_provenance as fp

    assert provider in fp.FIND_PROVIDERS


def _approve_nara(tmp_path, queued_finds, object_urls):
    f = _record(tmp_path)
    with patch(
        "src.enrichment.acquisition_fetchers.nara_digital_objects",
        return_value=(
            None if object_urls is None else {"title": "t", "object_urls": object_urls}
        ),
    ):
        with _search([(NARA, "nara_catalog")]):
            _run(tmp_path, {"nara_api_key": "k"})
    (e,) = queued_finds("nara-find")
    fetch_review.set_review_status(e["fetch_event_id"], "affirmed")
    with _search([(NARA, "nara_catalog")]):
        _run(tmp_path, {"nara_api_key": "k"})
    return json.loads(f.read_text())


def test_an_approved_nara_record_with_scans_is_online(tmp_path, queued_finds):
    rec = _approve_nara(
        tmp_path, queued_finds, ["https://catalog.archives.gov/medialz/a.tif"]
    )
    (finding,) = rec["source_metadata"]["findings"]
    assert finding["naId"] == "305271" and finding["digital_objects"] == 1
    assert rec["availability"] == "online" and rec["resource_urls"] == [NARA]


def test_an_approved_nara_record_without_scans_is_held_on_paper(tmp_path, queued_finds):
    rec = _approve_nara(tmp_path, queued_finds, [])
    assert rec["availability"] == "archive"  # goes on the NARA pull list
    assert rec["source_metadata"]["findings"][0]["digital_objects"] == 0


def test_an_unknown_scan_count_is_not_called_paper_only(tmp_path, queued_finds):
    rec = _approve_nara(tmp_path, queued_finds, None)
    assert rec["availability"] == "online"
    assert rec["source_metadata"]["findings"][0]["digital_objects"] is None


def test_an_empty_nara_answer_is_an_unknown_scan_count_not_zero(monkeypatch):
    from src.enrichment import acquisition_fetchers as af

    monkeypatch.setattr(
        af, "_fetch_nara_record", lambda n, k: {"body": {"hits": {"hits": []}}}
    )
    assert af.nara_digital_objects("305271", "k") is None
    monkeypatch.setattr(
        af,
        "_fetch_nara_record",
        lambda n, k: {"record": {"naId": 999, "title": "another record"}},
    )
    assert af.nara_digital_objects("305271", "k") is None  # someone else's record
    monkeypatch.setattr(
        af,
        "_fetch_nara_record",
        lambda n, k: {
            "record": {"naId": 305271, "title": "XIV Corps", "digitalObjects": []}
        },
    )
    got = af.nara_digital_objects("305271", "k")
    assert got["title"] == "XIV Corps" and got["object_urls"] == []


def test_a_waiting_record_is_not_flagged_unresolvable():
    from src.utils.semantic_flags import _citation_flags

    rec = {"title": "The Lorraine Campaign", "search_status": "pending"}
    assert "citation_unresolvable" not in (_citation_flags(rec) or [])


def test_a_paper_only_nara_record_with_another_copy_is_online():
    entry = {
        "source_metadata": {
            "findings": [
                {
                    "url": NARA,
                    "source": "nara_catalog",
                    "digital_objects": 0,
                    "verified_by": "grok+human",
                },
                {"url": ITEM, "source": "archive_org", "verified_by": "grok+human"},
            ]
        }
    }
    assert br._paper_only(entry) is False
    entry["source_metadata"]["findings"].pop()
    assert br._paper_only(entry) is True


def test_a_paper_only_record_is_never_planned_for_download():
    from src.enrichment.source_acquisition import plan_acquisition

    entry = {
        "search_status": "resolved",
        "availability": "archive",
        "search_source": "nara_catalog",
        "resource_urls": [NARA],
    }
    assert plan_acquisition(entry, {}) is None


def test_values_read_back_from_the_store_are_checked():
    event = {
        "endpoint": ITEM,
        "review_status": "affirmed",
        "request_params": {
            "search_source": "archive_org",
            "source_metadata": {
                "pdf_url": "file:///etc/passwd",
                "imagecount": 300,
                "evil": 1,
            },
            "download": {"naId": "../x", "object_count": True},
        },
    }
    assert bf.approved_details(event) == {"imagecount": 300}
    assert br._nara_fields(event["request_params"]["download"]) == {
        "naId": None,
        "digital_objects": None,
        "manifest_sha256": None,
        "vision_check": None,
    }
    bad = {**event, "endpoint": "http://127.0.0.1/x"}
    assert bf.approved_finds([bad]) == []


def test_a_partly_failed_record_is_searched_again_not_left_waiting(
    tmp_path, monkeypatch
):
    real = find_queue.enqueue_find
    monkeypatch.setattr(
        find_queue,
        "enqueue_find",
        lambda **k: find_queue.FAILED if "gutenberg" in k["url"] else real(**k),
    )
    f = _record(tmp_path)
    with _search(
        [(ITEM, "archive_org"), ("https://www.gutenberg.org/ebooks/1", "gutenberg")]
    ):
        stats = _run(tmp_path)
    assert stats["backend_down"] == 1
    assert json.loads(f.read_text()).get("search_status") != "pending"


def test_no_grok_means_no_nara_lookup():
    with patch("src.enrichment.acquisition_fetchers.nara_digital_objects") as look:
        bf.queue_findings(
            _entry(), [(NARA, "nara_catalog")], query="q", grok_client=None,
            config={"nara_api_key": "k"},
        )  # fmt: skip
    look.assert_not_called()


def test_finds_index_groups_bibliography_finds_by_record(queued_finds):
    _queue(_entry(), [(ITEM, "archive_org")])
    find_queue.enqueue_find(
        provider="openserp-find",
        url="https://example.org/p",
        verdict=find_queue.RELEVANT,
        origin=find_queue.Origin(
            entity_type="person", entity_id="01HPERSONAAAAAAAAAAAAAAAAA"
        ),
        summary="s",
    )
    idx = bf.finds_index()
    assert list(idx) == [BIB] and idx[BIB][0]["endpoint"] == ITEM


def test_an_approved_pdf_link_becomes_a_copy(tmp_path, queued_finds):
    f = _record(tmp_path)
    entry = json.loads(f.read_text())
    bf.queue_findings(
        entry, [(ITEM, "archive_org")], query="q", grok_client=object(), config={},
        details={"pdf_url": ITEM + "/x.pdf"},
    )  # fmt: skip
    (e,) = queued_finds("archive-org-find")
    fetch_review.set_review_status(e["fetch_event_id"], "affirmed")
    with _search([(ITEM, "archive_org")]):
        _run(tmp_path)
    assert json.loads(f.read_text())["resource_urls"] == [ITEM, ITEM + "/x.pdf"]


def test_a_series_with_no_scans_of_its_own_is_unknown_not_paper_only(queued_finds):
    with patch(
        "src.enrichment.acquisition_fetchers.nara_digital_objects",
        return_value={
            "title": "s",
            "object_urls": [],
            "level": "series",
            "scans_known": False,
        },
    ):
        _queue(_entry(), [(NARA, "nara_catalog")], config={"nara_api_key": "k"})
    (e,) = queued_finds("nara-find")
    assert e["request_params"]["download"]["object_count"] is None


def test_a_downloadable_copy_is_preferred_as_the_primary():
    nara = {
        "endpoint": NARA, "review_status": "affirmed", "queried_at": "2026-01-01",
        "request_params": {"search_source": "nara_catalog", "download": {"object_count": 0}},
    }  # fmt: skip
    item = {
        "endpoint": ITEM, "review_status": "affirmed", "queried_at": "2026-02-01",
        "request_params": {"search_source": "archive_org"},
    }  # fmt: skip
    assert [e["endpoint"] for e in bf.approved_finds([nara, item])] == [ITEM, NARA]


def test_the_cleaned_url_is_what_goes_on_the_record():
    e = {"endpoint": "HTTPS://Archive.org/details/x#frag", "review_status": "affirmed"}
    (got,) = bf.approved_finds([e])
    assert got["endpoint"] == "https://archive.org/details/x"
