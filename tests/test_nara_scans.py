"""NARA scans: the URLs and file details a download would fetch, checked and fingerprinted, and
Grok vision looking at the pages before a person sees the find (owner 2026-11-22)."""

import io
import json
from unittest import mock

import pytest

from src.enrichment import acquisition_fetchers as af
from src.enrichment import bibliography_finds as bf
from src.enrichment import nara_vision as nv

MEDIA = "https://catalog.archives.gov/medialive/87/767/6076787/content/dc-metro/anrc/5897359/box-11"


def _obj(n, kind="Image (JPG)", size=2412261, url=None):
    return {
        "objectUrl": url or f"{MEDIA}/hd1-253935742-2016-{n:03d}-ac.jpg",
        "objectFilename": f"hd1-253935742-2016-{n:03d}-ac.jpg",
        "objectType": kind,
        "objectFileSize": size,
        "objectId": str(1000 + n),
        "objectCreatedDate": "2016-05-01",
    }


# the shape of a live reply (naId 6076787, "461.2 World War II Awards", 96 scans; 2026-11-22)
LIVE = {
    "naId": 6076787,
    "title": "461.2 World War II Awards",
    "levelOfDescription": "fileUnit",
    "digitalObjects": [_obj(n) for n in range(1, 97)],
}


def test_every_scan_is_kept_with_its_file_details_and_a_fingerprint():
    got = af.nara_scans(LIVE)
    assert len(got["scans"]) == 96 and got["refused"] == 0
    first = got["scans"][0]
    assert first == {
        "url": f"{MEDIA}/hd1-253935742-2016-001-ac.jpg",
        "filename": "hd1-253935742-2016-001-ac.jpg",
        "type": "Image (JPG)",
        "size": 2412261,
    }
    assert got["total_bytes"] == 96 * 2412261 and got["types"] == ["Image (JPG)"]
    assert len(got["manifest_sha256"]) == 64
    assert af.nara_scans(LIVE)["manifest_sha256"] == got["manifest_sha256"]  # stable


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/medialive/x.jpg",
        "http://catalog.archives.gov/medialive/x.jpg",  # not https
        "https://catalog.archives.gov/id/123",  # not a media path
        "https://catalog.archives.gov.evil.example/medialive/x.jpg",
        "https://127.0.0.1/medialive/x.jpg",
        "javascript:alert(1)",
        "https://catalog.archives.gov/medialive/a\nb.jpg",
        "https://catalog.archives.gov/medialive/../id/x.jpg",  # dot segment
        "https://catalog.archives.gov/medialive/%2e%2e/x.jpg",  # escaped dot segment
        "https://catalog.archives.gov/medialive/x.jpg;a=b",  # path parameter
        "https://catalog.archives.gov:8443/medialive/x.jpg",  # another port
        "https://catalog.archives.gov:abc/medialive/x.jpg",  # a port that is not a number
        "https://u:p@catalog.archives.gov/medialive/x.jpg",  # credentials
        "https://:p@catalog.archives.gov/medialive/x.jpg",  # a password only
        "https://catalog.archives.gov/medialive/x.jpg?u=https://evil.example",  # a query
        "https://catalog.archives.gov/medialive/x.jpg#f",  # a fragment
        "https://catalog.archives.gov/medialive/a\\..\\x.jpg",  # backslashes
        ["https://catalog.archives.gov/medialive/x.jpg"],
        None,
    ],
)
def test_a_scan_url_off_nara_media_is_refused(url):
    got = af.nara_scans({"digitalObjects": [_obj(1), {**_obj(2), "objectUrl": url}]})
    assert [s["filename"] for s in got["scans"]] == ["hd1-253935742-2016-001-ac.jpg"]
    assert got["refused"] == 1


def test_both_nara_media_paths_are_accepted():
    for path in ("/medialive/", "/medialz/"):
        assert af.nara_scan_url_ok(f"https://catalog.archives.gov{path}a/b.tif")


@pytest.mark.parametrize("size", ["²", "1" * 50, -1, True, [3], "12.5"])
def test_an_odd_size_is_unknown_not_a_crash(size):
    (scan,) = af.nara_scans({"digitalObjects": [_obj(1, size=size)]})["scans"]
    assert scan["size"] is None


def test_refused_urls_are_said(monkeypatch, caplog):
    record = {
        "naId": 7,
        "title": "t",
        "digitalObjects": [_obj(1, url="https://evil.example/x.jpg")],
    }
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": record})
    af.nara_digital_objects("7", "k")
    assert "OBSERV nara.scan_urls_refused naId=7 refused=1" in caplog.text


def test_the_find_shows_what_approval_would_bring_in(monkeypatch):
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE})
    monkeypatch.setattr(
        nv, "check_scans", lambda *a: {"verdict": "unsure", "reason": "", "pages": []}
    )
    d = bf.nara_download_details(
        "https://catalog.archives.gov/id/6076787", "k", "cit", object()
    )
    assert d["object_count"] == 96 and d["total_bytes"] == 96 * 2412261
    assert len(d["scans"]) == 20 and d["scans"][0]["filename"].endswith("001-ac.jpg")
    assert d["types"] == ["Image (JPG)"] and len(d["manifest_sha256"]) == 64


def test_a_batch_round_does_not_spend_nara_quota_again(monkeypatch):
    calls = []
    monkeypatch.setattr(
        af, "_fetch_nara_record", lambda n, k: calls.append(n) or {"record": LIVE}
    )
    monkeypatch.setattr(
        nv, "check_scans", lambda *a: {"verdict": "unsure", "reason": "", "pages": []}
    )
    for _ in range(3):
        bf.nara_download_details(
            "https://catalog.archives.gov/id/6076787", "k", "c", object()
        )
    assert calls == ["6076787"]


# --- the download fetches exactly what a person approved ----------------------------------------


def test_the_download_stops_when_nara_changed_the_scan_list(monkeypatch, caplog):
    approved = af.nara_scans(LIVE)["manifest_sha256"]
    entry = {
        "resource_urls": ["https://catalog.archives.gov/id/6076787"],
        "source_metadata": {
            "findings": [{"naId": "6076787", "manifest_sha256": approved}]
        },
    }
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE})
    target = af._nara_fetcher(entry, {"nara_api_key": "k"})
    assert target is not None and target.pages == 96
    changed = {**LIVE, "digitalObjects": LIVE["digitalObjects"][:95]}
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": changed})
    assert af._nara_fetcher(entry, {"nara_api_key": "k"}) is None
    assert "OBSERV acquisition.nara_scans_changed naId=6076787" in caplog.text


def test_the_approved_fingerprint_goes_on_the_record():
    import src.enrichment.bibliography_resolver as br

    fp = "a" * 64
    got = br._nara_fields(
        {
            "naId": "1",
            "object_count": 3,
            "manifest_sha256": fp,
            "vision": {"verdict": "match"},
        }
    )
    assert got["manifest_sha256"] == fp and got["vision_check"] == "match"
    assert (
        br._nara_fields({"manifest_sha256": "x" * 64, "vision": {"verdict": "evil"}})[
            "manifest_sha256"
        ]
        is None
    )


# --- Grok vision looks at the pages -------------------------------------------------------------


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


class _Grok:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def extract_json_with_image_base64(self, **k):
        self.asked.append(k)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.setattr(nv, "_fetch", lambda url: _png())


SCANS = af.nara_scans(LIVE)["scans"]


def test_the_first_and_a_middle_page_are_checked():
    picked = nv.pages_to_check(SCANS)
    assert [p["page"] for p in picked] == [1, 49]
    assert nv.pages_to_check(SCANS[:2]) == [dict(SCANS[0], page=1)]
    pdf = [{"url": f"{MEDIA}/a.pdf", "filename": "a.pdf", "type": "PDF"}]
    assert nv.pages_to_check(pdf) == []


@pytest.mark.parametrize(
    "answers,expected",
    [
        (({"verdict": "match", "reason": "Header: 3rd Armored, Aug 44"}, {"verdict": "unsure", "reason": "blank"}), "match"),
        (({"verdict": "not_match", "reason": "XIX Corps"}, {"verdict": "not_match", "reason": "XIX Corps"}), "not_match"),
        (({"verdict": "match", "reason": "x"}, {"verdict": "not_match", "reason": "y"}), "unsure"),
        (({"verdict": "maybe"}, {"bad": 1}), "unsure"),
        ((RuntimeError("down"), {"verdict": "unsure", "reason": ""}), "unsure"),
    ],
)  # fmt: skip
def test_page_verdicts_combine(pages, answers, expected):
    grok = _Grok(*answers)
    got = nv.check_scans(
        SCANS, "3rd Armored Div AAR, Aug 44", {"nara_title": "t"}, grok
    )
    assert got["verdict"] == expected and len(got["pages"]) == 2
    assert all(k["cache_type"] == "nara_scan_vision" for k in grok.asked)
    assert (
        got["pages"][0]["url"] == SCANS[0]["url"]
    )  # the reviewer sees the page Grok saw


def test_one_refused_page_and_one_not_looked_at_is_unsure(monkeypatch):
    # the page that could not be fetched is no evidence against the document: the find is queued
    fetched = iter([_png(), None])
    monkeypatch.setattr(nv, "_fetch", lambda url: next(fetched))
    got = nv.check_scans(
        SCANS, "c", {}, _Grok({"verdict": "not_match", "reason": "XIX"})
    )
    assert [p["verdict"] for p in got["pages"]] == ["not_match", "unchecked"]
    assert got["verdict"] == "unsure"


def test_the_memos_forget_the_oldest_record_first(monkeypatch):
    monkeypatch.setattr(bf, "_MEMO_MAX", 2)
    memo = {}
    for k in ("a", "b", "b", "c"):
        bf._remember(memo, k, k)
    assert list(memo) == ["b", "c"]  # re-remembering "b" did not push out "a" early


def test_no_image_pages_or_no_grok_is_unchecked(pages):
    pdf = [{"url": f"{MEDIA}/a.pdf", "filename": "a.pdf", "type": "PDF"}]
    assert nv.check_scans(pdf, "c", {}, _Grok())["verdict"] == "unchecked"
    assert nv.check_scans(SCANS, "c", {}, None)["verdict"] == "unchecked"


def test_a_page_that_cannot_be_fetched_is_unchecked(monkeypatch):
    monkeypatch.setattr(nv, "_fetch", lambda url: None)
    got = nv.check_scans(SCANS[:1], "c", {}, _Grok())
    assert got["verdict"] == "unchecked" and "fetched" in got["pages"][0]["reason"]


def test_a_batch_queued_vision_request_is_not_an_answer(pages):
    from src.grok_client import BatchModeCollecting

    with pytest.raises(BatchModeCollecting):
        nv.check_scans(SCANS, "c", {}, _Grok(BatchModeCollecting("queued")))


def test_both_pages_are_queued_in_one_batch_round(pages):
    from src.grok_client import BatchModeCollecting

    grok = _Grok(BatchModeCollecting("queued"), BatchModeCollecting("queued"))
    with pytest.raises(BatchModeCollecting):
        nv.check_scans(SCANS, "c", {}, grok)
    assert len(grok.asked) == 2  # the second page was not left for another round


def test_a_download_without_an_approved_fingerprint_is_said(monkeypatch, caplog):
    entry = {
        "resource_urls": ["https://catalog.archives.gov/id/6076787"],
        "source_metadata": {"findings": [{"naId": "6076787"}]},
    }
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE})
    assert af._nara_fetcher(entry, {"nara_api_key": "k"}) is not None
    assert "OBSERV acquisition.nara_scans_unpinned naId=6076787" in caplog.text


def test_a_batch_round_then_the_answer_queues_the_find_once(monkeypatch, queued_finds):
    from src.grok_client import BatchModeCollecting

    answers = [
        BatchModeCollecting("queued"),
        {"verdict": "match", "reason": "r", "pages": []},
    ]
    asked = []

    def check(*a):
        asked.append(a)
        got = answers[min(len(asked), len(answers)) - 1]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE})
    monkeypatch.setattr(nv, "check_scans", check)
    entry = {"BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW", "title": "x"}
    args = dict(
        query="461.2 World War II Awards",
        grok_client=object(),
        config={"nara_api_key": "k"},
    )
    found = [("https://catalog.archives.gov/id/6076787", "nara_catalog")]
    with pytest.raises(BatchModeCollecting):
        bf.queue_findings(entry, found, **args)
    assert queued_finds("nara-find") == []  # nothing is queued before the answer
    bf.queue_findings(entry, found, **args)
    bf.queue_findings(entry, found, **args)  # the pass repeats
    assert len(queued_finds("nara-find")) == 1
    assert len(asked) == 2  # the remembered verdict is not asked for again


def _queue(monkeypatch, verdict, queued_finds):
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE})
    monkeypatch.setattr(
        nv, "check_scans", lambda *a: {"verdict": verdict, "reason": "r", "pages": []}
    )
    entry = {"BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW", "title": "x"}
    got = bf.queue_findings(
        entry,
        [("https://catalog.archives.gov/id/6076787", "nara_catalog")],
        query="461.2 World War II Awards",
        grok_client=object(),
        config={"nara_api_key": "k"},
    )
    return got, queued_finds("nara-find")


def test_scans_that_are_not_the_document_are_not_queued(
    monkeypatch, queued_finds, caplog
):
    got, finds = _queue(monkeypatch, "not_match", queued_finds)
    assert got.refused == 1 and finds == []
    assert "OBSERV bib_finds.nara_scans_not_the_document" in caplog.text


@pytest.mark.parametrize("verdict", ["match", "unsure", "unchecked"])
def test_other_verdicts_are_queued_with_the_verdict_for_the_reviewer(
    monkeypatch, queued_finds, verdict
):
    got, (event,) = _queue(monkeypatch, verdict, queued_finds)
    assert got.queued == 1
    assert event["request_params"]["download"]["vision"]["verdict"] == verdict


def test_the_vision_cache_kind_echoes_its_request_id():
    from src.utils.request_identity import is_request_bound

    assert is_request_bound("nara_scan_vision")


def test_the_prompt_renders_with_the_page_and_the_citation():
    page = {"url": SCANS[0]["url"], "page": 1}
    text = nv._prompt(
        page, "3rd Armored AAR", {"nara_title": "461.2", "series": {"title": "Awards"}}
    )
    assert "page 1" in text and "3rd Armored AAR" in text and '"verdict"' in text


def test_a_live_scan_url_is_trusted_as_nara():
    from src.utils import link_policy

    assert link_policy.trust_basis(SCANS[0]["url"]) == "nara"
