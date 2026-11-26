"""Web photos (owner 2026-11-23): an OpenSERP image search, a batched text pre-filter, the photo and
its page downloaded and kept (page without active content, photo in the AV staging area), the same
bytes never judged twice for an entity, and every kept photo confirmed by Grok vision.
"""

import io
from pathlib import Path
import logging
from unittest import mock

import pytest

import src.enrichment.openserp_enrichment as oe
from src.enrichment import web_photos as wp
from src.utils import find_pages
from src.utils.html_safety import strip_active_content

PAGE = "https://example.org/shermans"
SUBJECT = wp.Subject(
    entity_key="equipment#01HEQUIPMENT0000000000000",
    text_context="Photo of M4 Sherman WWII",
    subject="M4 Sherman (tank, United States)",
    what_counts="the M4 Sherman - a WWII photograph of it or a preserved example",
)


def _png(color="white", size=(40, 40)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _pattern(seed):
    """A distinct photo-like image (random blocks): different perceptual hashes per seed."""
    import random

    from PIL import Image

    rnd = random.Random(seed)
    img = Image.new("L", (64, 64))
    img.putdata([rnd.choice((0, 255)) for _ in range(64 * 64)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _result(n, page=PAGE):
    return {
        "url": f"https://img.example.org/sherman{n}.png",
        "title": f"M4 Sherman {n}",
        "description": "",
        "page_url": page,
    }


class _Grok:
    """Text pre-filter answers (``extract_json``) and vision answers (``..._image_base64``)."""

    def __init__(self, prefilter=None, vision=None):
        self.prefilter = prefilter
        self.vision = list(vision or [])
        self.text_asked, self.vision_asked = [], []

    def extract_json(self, prompt, **k):
        self.text_asked.append(prompt)
        n = prompt.count("\n[")
        return {"verdicts": self.prefilter or ["YES"] * n}

    def clear_cache_entry(self, *a, **k):
        pass

    def extract_json_with_image_base64(self, **k):
        self.vision_asked.append(k)
        answer = self.vision.pop(0) if self.vision else {"relevant": "yes"}
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def web(monkeypatch):
    """Images by URL, one page holding them; counts downloads."""
    images, fetched = {}, []
    page_html = {
        PAGE: "<html><head><title>Shermans in Normandy</title></head><body><figure>"
        '<img src="https://img.example.org/sherman1.png" alt="A Sherman">'
        "<figcaption>M4 Sherman, Normandy, July 1944</figcaption></figure>"
        '<img src="/sherman2.png" alt="Second tank"><script>steal()</script></body></html>'
    }

    def fetch_image(url):
        fetched.append(url)
        body = images.get(url)
        return (body, "png", "ok") if body else (None, None, "fetch_error")

    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes_with_reason", fetch_image
    )
    monkeypatch.setattr(
        "src.utils.url_hygiene.fetch_text_safe",
        lambda url, **k: (page_html.get(url), 200 if url in page_html else 404),
    )
    oe.reset_metrics()
    yield images, fetched
    oe.reset_metrics()


def test_a_confirmed_photo_is_kept_with_its_provenance(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(
        vision=[{"relevant": "yes", "period_photo": "yes", "reason": "a Sherman"}]
    )
    (photo,) = wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    assert photo["url"] == "https://img.example.org/sherman1.png"
    assert photo["source_page_url"] == PAGE
    assert photo["caption"] == "M4 Sherman, Normandy, July 1944"  # the figcaption
    assert photo["vision_check"] == "verified" and photo["vision_verified"] is True
    assert photo["period_photo"] == "yes"
    assert photo["copyright_status"] == "assumed_public_domain"
    assert photo["sha256"] == find_pages.content_sha256(images[photo["url"]])
    assert photo["local_path"].startswith("output/photos/files/")
    assert photo["page_sha256"]  # the page is kept
    kept = find_pages.load({"page_sha256": photo["page_sha256"]})
    assert b"<script" not in kept and b"Normandy" in kept  # kept without its JavaScript
    assert grok.vision_asked[0]["cache_type"] == "photo_relevance_vision"
    assert "M4 Sherman, Normandy, July 1944" in grok.vision_asked[0]["prompt"]


def test_in_aws_a_photo_is_written_only_to_the_staging_area(monkeypatch):
    from src.ingestion.av_scan import _prod_path

    puts = []

    class _S3:
        class exceptions:
            ClientError = KeyError

        def head_object(self, Bucket, Key):
            raise KeyError(Key)

        def put_object(self, Bucket, Key, Body):
            puts.append(Key)

    monkeypatch.setattr(find_pages, "_bucket", lambda: "dev-wwii-data-pipeline")
    monkeypatch.setattr("boto3.client", lambda *a, **k: _S3())
    got = find_pages.keep_photo(_png(), "png")
    (key,) = puts
    assert key.startswith("unscanned/output/photos/files/")
    assert _prod_path(key) == got["local_path"]  # where a clean scan promotes it


def test_the_same_photo_from_another_site_is_a_duplicate(web):
    images, _ = web
    body = _png()
    images["https://img.example.org/sherman1.png"] = body
    images["https://other.example.com/copy.png"] = body  # the same bytes elsewhere
    grok = _Grok()
    copy = {**_result(1), "url": "https://other.example.com/copy.png"}
    photos = wp.find_photos([_result(1), copy], SUBJECT, grok, [], 3)
    assert len(photos) == 1 and len(grok.vision_asked) == 1
    assert oe.get_metrics()["photos_duplicate"] == 1


def test_a_resized_copy_is_caught_by_the_perceptual_hash(web):
    images, _ = web
    from PIL import Image

    original = _pattern(7)
    bigger = io.BytesIO()
    Image.open(io.BytesIO(original)).resize((128, 128)).save(bigger, format="PNG")
    assert bigger.getvalue() != original  # different bytes, the same picture
    images["https://img.example.org/sherman1.png"] = original
    images["https://img.example.org/sherman2.png"] = bigger.getvalue()
    grok = _Grok()
    photos = wp.find_photos([_result(1), _result(2)], SUBJECT, grok, [], 3)
    assert len(photos) == 1 and oe.get_metrics()["photos_duplicate"] == 1


def test_a_photo_on_the_record_already_is_not_downloaded_again(web):
    images, fetched = web
    on_record = [{"url": "https://img.example.org/sherman1.png"}]
    assert wp.find_photos([_result(1)], SUBJECT, _Grok(), on_record, 3) == []
    assert fetched == []


def test_bytes_judged_for_this_entity_are_not_judged_again(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"relevant": "no", "reason": "a Panther"}])
    assert wp.find_photos([_result(1)], SUBJECT, grok, [], 3) == []
    elsewhere = {**_result(1), "url": "https://mirror.example.net/s.png"}
    images[elsewhere["url"]] = images["https://img.example.org/sherman1.png"]
    assert wp.find_photos([elsewhere], SUBJECT, grok, [], 3) == []
    assert len(grok.vision_asked) == 1  # the same bytes: the cached "no"
    assert oe.get_metrics()["photos_verdict_cached"] == 1


def test_another_entity_is_asked_about_the_same_bytes(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"relevant": "no"}, {"relevant": "yes"}])
    assert wp.find_photos([_result(1)], SUBJECT, grok, [], 3) == []
    other = wp.Subject(
        "equipment#01HOTHER00000000000000000", "Photo of M4A3", "M4A3", "M4A3"
    )
    assert len(wp.find_photos([_result(1)], other, grok, [], 3)) == 1
    assert len(grok.vision_asked) == 2


def test_the_text_prefilter_drops_junk_before_any_download(web):
    images, fetched = web
    for n in (1, 2):
        images[f"https://img.example.org/sherman{n}.png"] = _pattern(n)
    grok = _Grok(prefilter=["NO", "YES"])
    photos = wp.find_photos([_result(1), _result(2)], SUBJECT, grok, [], 3)
    assert [p["url"] for p in photos] == ["https://img.example.org/sherman2.png"]
    assert fetched == ["https://img.example.org/sherman2.png"]
    assert len(grok.text_asked) == 1  # one batched request for the query's results
    assert oe.get_metrics()["photos_prefiltered_out"] == 1


def test_no_usable_vision_answer_leaves_the_entity_unsearched(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"verdict": "maybe"}])
    with pytest.raises(oe.VerifyFailed):
        wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    grok = _Grok(vision=[RuntimeError("grok down")])
    with pytest.raises(oe.VerifyFailed):
        wp.find_photos([_result(1)], SUBJECT, grok, [], 3)


def test_a_batch_queued_vision_request_is_not_an_answer(web):
    from src.grok_client import BatchModeCollecting

    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[BatchModeCollecting("queued")])
    with pytest.raises(BatchModeCollecting):
        wp.find_photos([_result(1)], SUBJECT, grok, [], 3)


def test_unsure_and_not_an_image_are_not_kept(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    images["https://img.example.org/sherman2.png"] = b"<html>not an image</html>" * 50
    grok = _Grok(vision=[{"relevant": "unsure"}])
    assert wp.find_photos([_result(1), _result(2)], SUBJECT, grok, [], 3) == []
    assert len(grok.vision_asked) == 1  # the HTML was never sent to vision


def test_the_cap_stops_the_downloads(web):
    images, fetched = web
    for n in range(1, 5):
        images[f"https://img.example.org/sherman{n}.png"] = _pattern(n)
    photos = wp.find_photos([_result(n) for n in range(1, 5)], SUBJECT, _Grok(), [], 2)
    assert len(photos) == 2 and len(fetched) == 2


def test_a_page_that_cannot_be_fetched_leaves_the_result_title_as_caption(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    (photo,) = wp.find_photos(
        [_result(1, page="https://gone.example.org/x")], SUBJECT, _Grok(), [], 3
    )
    assert photo["caption"] == "M4 Sherman 1" and photo["page_sha256"] is None


def test_a_photo_that_cannot_be_stored_leaves_the_entity_unsearched(web, monkeypatch):
    from src.utils.find_queue import FindNotStored

    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    real = find_pages.keep_photo
    down = {"on": True}
    monkeypatch.setattr(
        find_pages,
        "keep_photo",
        lambda body, ext: {"photo_kept": False} if down["on"] else real(body, ext),
    )
    grok = _Grok()
    with pytest.raises(FindNotStored):
        wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    down["on"] = (
        False  # storage is back: the cached "yes" keeps it without asking Grok again
    )
    assert len(wp.find_photos([_result(1)], SUBJECT, grok, [], 3)) == 1
    assert len(grok.vision_asked) == 1


def test_a_cached_rejection_fetches_no_page(web, monkeypatch):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    pages = []
    real = wp._page
    monkeypatch.setattr(wp, "_page", lambda r: pages.append(r) or real(r))
    grok = _Grok(vision=[{"relevant": "no"}])
    wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    assert len(pages) == 1
    wp.find_photos(
        [{**_result(1), "url": "https://m.example.net/s.png"}], SUBJECT, grok, [], 3
    )
    assert len(pages) == 1  # the second copy: the cached "no", no page fetched


@pytest.mark.parametrize(
    "bad", ['{"relevant": "maybe"}', '"not a verdict"', "{not json"]
)
def test_a_bad_cached_verdict_is_asked_again(web, bad):
    from src.utils import search_cache

    images, _ = web
    body = _png()
    images["https://img.example.org/sherman1.png"] = body
    key = wp._verdict_key(find_pages.content_sha256(body), SUBJECT)
    search_cache.cache_result(wp._VERDICT_SOURCE, key, bad)
    grok = _Grok()
    assert len(wp.find_photos([_result(1)], SUBJECT, grok, [], 3)) == 1
    assert len(grok.vision_asked) == 1


def test_an_eps_file_posing_as_an_image_is_never_decoded(monkeypatch):
    from PIL import EpsImagePlugin

    from src.extraction.equipment import _prepare_image_for_vision

    def ghostscript(*a, **k):
        raise AssertionError("the EPS decoder was reached")

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", ghostscript)
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n"
    prepared, problem = _prepare_image_for_vision(eps)
    assert prepared is None and problem


def test_the_caption_is_matched_by_file_name_when_the_src_is_relative():
    html = '<img src="/media/tank.jpg" alt="Sherman at Bastogne">'
    caption, _ = wp.caption_for(html, "https://cdn.example.org/media/tank.jpg", PAGE)
    assert caption == "Sherman at Bastogne"


# --- JavaScript is stripped from every kept page ---------------------------------------------


@pytest.mark.parametrize(
    "html,gone",
    [
        ("<p>x</p><script>alert(1)</script>", "alert"),
        ('<img src="a.png" onerror="alert(1)">', "onerror"),
        ('<a href="javascript:alert(1)">x</a>', "javascript"),
        ('<a href="  JaVa\tScRiPt:alert(1)">x</a>', "ScRiPt"),
        ('<iframe src="https://evil.example"></iframe>', "iframe"),
        ('<meta http-equiv="refresh" content="0;url=https://evil.example">', "refresh"),
        ('<object data="data:text/html,x"></object>', "object"),
        ('<base href="https://evil.example/">', "base"),
    ],
)
def test_active_content_is_removed(html, gone):
    assert gone not in strip_active_content(html)


def test_a_page_with_nothing_active_keeps_its_content():
    html = (
        "<html><body><p>Patton at Bastogne</p><img src='a.jpg' alt='x'></body></html>"
    )
    safe = strip_active_content(html)
    assert "Patton at Bastogne" in safe and 'src="a.jpg"' in safe and 'alt="x"' in safe
    got = find_pages.keep("https://example.org/p", html)
    assert find_pages.load(got) == safe.encode()


# the bypasses the 2026-11-23 security review found against an html.parser-based strip, and the
# usual ones: a browser parsing the KEPT page must find nothing that runs
_PAYLOADS = [
    "<!--><script>alert(1)</script>-->",
    "<![CDATA[><script>alert(1)</script>]]>",
    "<svg><desc><![CDATA[</desc><script>alert(1)</script>]]></desc></svg>",
    '<a href="javascript:alert(1)" href="http://ok">x</a>',
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<math><mi><style><img src=x onerror=alert(1)></style></mi></math>",
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="java&Tab;script:alert(1)">x</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>',
    '<img srcset="ok.png 1x, javascript:alert(1) 2x">',
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<div style="width:expression(alert(1))">x</div>',
    "<template><script>alert(1)</script></template>",
    "<textarea></textarea><script>alert(1)</script>",
    "<title></title><img src=x onerror=alert(1)></title>",
    "<a/onclick=alert(1)>x</a>",
]


def _active_in(html):
    """What a browser (the HTML5 algorithm) would find active in ``html``."""
    from bs4 import BeautifulSoup

    found = []
    for tag in BeautifulSoup(html, "html5lib").find_all(True):
        if tag.name in (
            "script",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
            "style",
            "base",
        ):
            found.append(tag.name)
        for name, value in tag.attrs.items():
            text = str(value).lower().replace(" ", "")
            if (
                name.lower().startswith("on")
                or name == "srcdoc"
                or "javascript" in text
            ):
                found.append(f"{tag.name}@{name}")
            if "expression(" in text:
                found.append(f"{tag.name}@{name}")
    return found


def test_a_page_over_the_size_limit_is_refused_at_once(caplog):
    import time

    from src.utils import observe
    from src.utils.html_safety import PageTooComplex

    observe._counts.clear()
    html = "<p>" + "x" * (3 * 1024 * 1024) + "</p>"
    started = time.monotonic()
    with pytest.raises(PageTooComplex):
        strip_active_content(html)
    assert time.monotonic() - started < 1  # never parsed
    with caplog.at_level(logging.WARNING):
        assert find_pages.keep("https://example.org/big", html) == {"page_kept": False}
    assert "page_too_complex" in caplog.text


def test_a_page_that_would_take_too_long_is_stopped_at_the_budget():
    """The reviewers' slow pages cannot hold a worker: the sanitizer is killed at the budget."""
    import time

    from src.utils import html_safety
    from src.utils.html_safety import PageTooComplex

    slow = (
        "<div>" * 150_000 + "x"
    )  # ~30 s for html5ever; 0.75 MB, inside the size limit
    started = time.monotonic()
    with pytest.raises(PageTooComplex, match="within"):
        strip_active_content(slow)
    assert time.monotonic() - started < html_safety.BUDGET_S + 3


@pytest.mark.parametrize(
    "html",
    ["<b></i>" * 16_000, "<b>" * 20_000 + "x", "<b><p></b></p>" * 30_000],
    ids=["stray-ends", "deep-b", "misnest"],
)
def test_the_review_slow_pages_finish_well_inside_the_budget(html):
    import time

    from src.utils import html_safety

    started = time.monotonic()
    try:
        assert "<script" not in strip_active_content(html)
    except html_safety.PageTooComplex:
        pass  # refused (too deep for later parsers): just as good, and as quick
    assert time.monotonic() - started < html_safety.BUDGET_S


def test_a_page_nesting_too_deep_for_later_parsers_is_refused():
    from src.utils.html_safety import MAX_DEPTH, PageTooComplex

    with pytest.raises(PageTooComplex, match="deep"):
        strip_active_content("<div>" * 30_000 + "x")  # html5lib on the result: 15 s
    ok = "<div>" * (MAX_DEPTH - 5) + "x" + "</div>" * (MAX_DEPTH - 5)
    assert "x" in strip_active_content(ok)


def test_the_sanitizer_sees_none_of_our_environment(monkeypatch):
    from src.utils import html_safety

    monkeypatch.setenv("GROK_API_KEY", "secret-key")
    monkeypatch.setattr(
        html_safety,
        "_WORKER",
        "import os, sys; sys.stdout.write(os.environ.get('GROK_API_KEY', 'none'))",
    )
    assert html_safety._clean("<p>x</p>") == "none"


def test_non_ascii_text_survives_the_child_process():
    assert "Écouché – Straße" in strip_active_content("<p>Écouché – Straße</p>")


def test_a_sanitizer_crash_is_a_refusal_not_an_error(monkeypatch):
    from src.utils import html_safety
    from src.utils.html_safety import PageTooComplex

    monkeypatch.setattr(html_safety, "_WORKER", "import sys; sys.exit(3)")
    with pytest.raises(PageTooComplex, match="exited 3"):
        strip_active_content("<p>x</p>")


def test_the_title_is_kept_escaped_and_not_repeated_in_the_body():
    html = '<html><head><title>Shermans &amp; "Tigers"</title></head><body><p>Text</p></body></html>'
    safe = strip_active_content(html)
    assert "<title>Shermans &amp; &quot;Tigers&quot;</title>" in safe
    assert safe.count("Shermans") == 1


def test_an_ordinary_large_page_is_still_kept():
    html = ("<p>" + "word " * 60 + "</p>\n") * 5000  # ~1.5 MB of article text
    assert find_pages.keep("https://example.org/long", html)["page_kept"] is True


@pytest.mark.parametrize("payload", _PAYLOADS)
def test_a_browser_finds_nothing_active_in_a_kept_page(payload):
    safe = strip_active_content(payload)
    assert _active_in(safe) == []
    assert strip_active_content(safe) == safe  # stripping again changes nothing


def test_the_kept_page_checksum_is_of_the_stripped_page():
    got = find_pages.keep("https://example.org/p", "<p>a</p><script>x()</script>")
    body = find_pages.load(got)
    assert b"script" not in body and got["page_sha256"] == find_pages.content_sha256(
        body
    )


# --- the OpenSERP image search ---------------------------------------------------------------


def test_the_image_search_maps_the_image_and_its_page(monkeypatch):
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"openserp": {"rate_limit_seconds": 0}},
    )
    oe.reset_circuit()
    sent = []
    body = {
        "results": [
            {
                "title": "M4 Sherman",
                "image": {"url": "https://img.example.org/s.jpg", "width": 800},
                "source": {"page_url": PAGE, "domain": "example.org"},
            }
        ]
    }

    def fetch(*a, **k):
        sent.append(a[1])
        return mock.Mock(status_code=200, json=lambda: body)

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fetch)
    (r,) = oe._search_openserp_images("M4 Sherman photo", "http://serp")
    assert r == {
        "url": "https://img.example.org/s.jpg",
        "title": "M4 Sherman",
        "description": "",
        "page_url": PAGE,
    }
    assert sent == ["http://serp/mega/image"]
    oe._search_openserp(
        "M4 Sherman photo", "http://serp"
    )  # a web search is its own entry
    assert sent[-1] == "http://serp/mega/search"


def test_an_image_on_an_excluded_page_is_dropped(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_registry.is_domain_excluded", lambda u: "spam.example" in u
    )
    rows = [
        {"url": "https://cdn.example.org/a.jpg", "page_url": "https://spam.example/p"},
        {"url": "https://cdn.example.org/b.jpg", "page_url": PAGE},
    ]
    assert [r["url"] for r in oe._drop_excluded_domains(rows)] == [
        "https://cdn.example.org/b.jpg"
    ]


# --- equipment uses it --------------------------------------------------------------------------


def test_equipment_photos_come_from_the_image_search(monkeypatch):
    seen = {}

    def find(results, subject, grok, existing, wanted, **k):
        seen.update(subject=subject, wanted=wanted)
        return [{"url": "https://img.example.org/s.png", "vision_verified": True}]

    monkeypatch.setattr(oe, "_search_openserp_images", lambda q, u: [_result(1)])
    monkeypatch.setattr(
        oe, "_search_openserp", lambda *a, **k: 1 / 0
    )  # never a web search
    monkeypatch.setattr(wp, "find_photos", find)
    monkeypatch.setattr(oe, "_entity_caps", lambda i=None, w=None: (3, 3))
    data = {"EquipmentID": "01HEQUIPMENT0000000000000", "common_name": "M4 Sherman"}
    assert oe.enrich_one_equipment(data, "http://serp", object()) is True
    assert data["images"][0]["vision_verified"] is True
    assert seen["subject"].entity_key == "equipment#01HEQUIPMENT0000000000000"


def test_equipment_without_an_id_is_not_photo_searched(monkeypatch, caplog):
    monkeypatch.setattr(oe, "_search_openserp_images", lambda q, u: 1 / 0)
    with caplog.at_level(logging.WARNING):
        assert oe.search_equipment_images("M4 Sherman", "http://serp", object()) == []
    assert "OBSERV openserp.equipment_without_id" in caplog.text


def test_a_web_photo_counts_as_vision_checked_in_the_equipment_step():
    from src.enrichment import equipment_images as ei

    photo = {
        "url": "u",
        "vision_verified": True,
        "vision_checked_at": "2026-11-23T00:00",
    }
    ei._queue_one(photo)
    assert photo["vision_check"] == ei.VERIFIED  # not asked about again


def test_a_web_photo_entry_is_valid_equipment_data():
    import jsonschema

    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    spec = next(x for x in ENTITY_REGISTRY if x.name == "equipment")
    schema = load_schema(spec)["properties"]["images"]
    photo = {
        "url": "https://img.example.org/s.png",
        "source": "openserp",
        "candidate_source": "openserp",
        "caption": "M4",
        "source_page_url": PAGE,
        "page_sha256": "sha256:" + "a" * 64,
        "sha256": "sha256:" + "b" * 64,
        "phash": "ffff0000ffff0000",
        "local_path": "output/photos/files/b.png",
        "vision_check": "verified",
        "vision_verified": True,
        "vision_reason": "r",
        "vision_checked_at": "2026-11-23T00:00:00+00:00",
        "period_photo": "unsure",
        "copyright_status": "assumed_public_domain",
        "candidate_added_at": "2026-11-23T00:00:00+00:00",
    }
    jsonschema.validate([photo], schema)


def test_the_photo_vision_kind_echoes_its_request_id():
    from src.utils.request_identity import is_request_bound

    assert is_request_bound("photo_relevance_vision")


def test_the_prompt_renders_with_the_subject_and_caption():
    from src.utils.prompt_loader import render_prompt

    text = render_prompt(
        "photo_relevance_vision",
        subject="M4 Sherman",
        what_counts="the tank",
        caption="Normandy 1944",
        page_title="Shermans",
    )
    assert "M4 Sherman" in text and "Normandy 1944" in text and '"period_photo"' in text


# --- nothing is stored for a "no"; the decision is (owner 2026-11-23) --------------------------


def _stored_pages():
    pages = find_pages._LOCAL_DIR / "pages"
    return sorted(p.name for p in pages.iterdir()) if pages.exists() else []


def test_a_no_stores_neither_the_photo_nor_its_page(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"relevant": "no", "reason": "a Panther"}])
    assert wp.find_photos([_result(1)], SUBJECT, grok, [], 3) == []
    assert _stored_pages() == []
    photos = find_pages._PHOTO_LOCAL_DIR
    assert not any(photos.iterdir())
    decided = wp.decision_for(
        "https://img.example.org/sherman1.png", SUBJECT.entity_key
    )
    assert decided["verdict"] == "no" and decided["reason"] == "a Panther"
    assert decided["page_url"] == PAGE and decided["sha256"].startswith("sha256:")


def test_a_batch_queued_vision_request_stores_no_page(web):
    from src.grok_client import BatchModeCollecting

    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    with pytest.raises(BatchModeCollecting):
        wp.find_photos(
            [_result(1)], SUBJECT, _Grok(vision=[BatchModeCollecting("q")]), [], 3
        )
    assert _stored_pages() == []  # the answer comes later: nothing kept meanwhile


def test_a_rejected_url_is_never_downloaded_again_for_that_entity(web):
    images, fetched = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"relevant": "no"}])
    wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    fetched.clear()
    assert wp.find_photos([_result(1)], SUBJECT, grok, [], 3) == []
    assert fetched == [] and len(grok.text_asked) == 1  # no download, no Grok request
    assert oe.get_metrics()["photos_ruled_out"] == 1


def test_another_entity_may_still_take_a_url_one_entity_rejected(web):
    images, fetched = web
    images["https://img.example.org/sherman1.png"] = _png()
    grok = _Grok(vision=[{"relevant": "no"}, {"relevant": "yes"}])
    wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    other = wp.Subject(
        "equipment#01HOTHER00000000000000000", "Photo of M4A3", "M4A3", "M4A3"
    )
    assert len(wp.find_photos([_result(1)], other, grok, [], 3)) == 1


@pytest.mark.parametrize(
    "body,why",
    [(None, "not_an_image"), (b"<html>not an image</html>" * 50, "undecodable")],
)
def test_a_url_that_is_not_a_usable_image_is_ruled_out_for_everyone(
    web, monkeypatch, body, why
):
    images, fetched = web
    url = "https://img.example.org/sherman1.png"
    if body is None:
        monkeypatch.setattr(
            "src.extraction.images.fetch_image_bytes_with_reason",
            lambda u: fetched.append(u) or (None, None, "not_an_image"),
        )
    else:
        images[url] = body
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    assert wp.decision_for(url)["verdict"] == "unusable"
    fetched.clear()
    other = wp.Subject(
        "equipment#01HOTHER00000000000000000", "Photo of M4A3", "M4A3", "M4A3"
    )
    assert wp.find_photos([_result(1)], other, _Grok(), [], 3) == []
    assert fetched == []  # nobody downloads it again


def test_a_timeout_rules_nothing_out(web, monkeypatch):
    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes_with_reason",
        lambda u: (None, None, "fetch_error"),
    )
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    assert wp.decision_for("https://img.example.org/sherman1.png") is None


def test_an_accepted_photo_is_recorded_and_rules_nothing_out(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    url = "https://img.example.org/sherman1.png"
    assert wp.decision_for(url, SUBJECT.entity_key)["verdict"] == "yes"
    assert not wp._ruled_out(url, SUBJECT)


def test_a_retracted_photo_and_its_copies_are_not_taken_again(web):
    images, fetched = web
    body = _pattern(3)
    images["https://img.example.org/sherman1.png"] = body
    (photo,) = wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    record = [photo, {"url": "https://other.example/keep.png"}]
    assert wp.retract(record, photo["url"], SUBJECT, "an M3 Lee, not a Sherman")
    assert [i["url"] for i in record] == ["https://other.example/keep.png"]
    fetched.clear()
    assert wp.find_photos([_result(1)], SUBJECT, _Grok(), record, 3) == []
    assert fetched == []  # the URL: ruled out
    copy = {**_result(1), "url": "https://mirror.example.net/s.png"}
    images[copy["url"]] = body
    grok = _Grok()
    assert wp.find_photos([copy], SUBJECT, grok, record, 3) == []
    assert (
        grok.vision_asked == []
    )  # a copy elsewhere: the bytes are a "no" for this entity


def test_the_retract_script_takes_a_photo_off_a_record(tmp_path):
    import json as _json

    from scripts import retract_photo

    d = tmp_path / "output" / "equipment"
    d.mkdir(parents=True)
    f = d / "M4_Sherman.json"
    rec = {
        "EquipmentID": "01HEQUIPMENT0000000000000",
        "common_name": "M4 Sherman",
        "images": [
            {"url": "https://img.example.org/s.png", "sha256": "sha256:" + "a" * 64}
        ],
    }
    f.write_text(_json.dumps(rec))
    args = [
        "--file",
        str(f),
        "--url",
        "https://img.example.org/s.png",
        "--reason",
        "M3 Lee",
    ]
    assert retract_photo.main(args + ["--dry-run"]) == 0
    assert _json.loads(f.read_text())["images"]  # a dry run changes nothing
    assert retract_photo.main(args) == 0
    assert _json.loads(f.read_text())["images"] == []
    decided = wp.decision_for(
        "https://img.example.org/s.png", "equipment#01HEQUIPMENT0000000000000"
    )
    assert decided["verdict"] == "retracted" and decided["reason"] == "M3 Lee"
    assert retract_photo.main(args) == 1  # nothing left to retract


def test_an_unresolvable_host_rules_nothing_out(web, monkeypatch):
    # the fetcher says "ssrf_blocked" when DNS fails too: never a block for everyone
    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes_with_reason",
        lambda u: (None, None, "ssrf_blocked"),
    )
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    assert wp.decision_for("https://img.example.org/sherman1.png") is None


def test_unusable_bytes_seen_again_elsewhere_are_ruled_out_for_everyone(web):
    images, _ = web
    junk = b"<html>not an image</html>" * 50
    images["https://img.example.org/sherman1.png"] = junk
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    copy = {**_result(1), "url": "https://mirror.example.net/j.png"}
    images[copy["url"]] = junk
    wp.find_photos(
        [copy], SUBJECT, _Grok(), [], 3
    )  # the cached verdict keeps "unusable"
    assert wp.decision_for(copy["url"])["verdict"] == "unusable"


def test_a_yes_whose_photo_could_not_be_stored_is_stored_next_run(web, monkeypatch):
    from src.utils.find_queue import FindNotStored

    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    real = find_pages.keep_photo
    down = {"on": True}
    monkeypatch.setattr(
        find_pages,
        "keep_photo",
        lambda b, e: {"photo_kept": False} if down["on"] else real(b, e),
    )
    grok = _Grok()
    with pytest.raises(FindNotStored):
        wp.find_photos([_result(1)], SUBJECT, grok, [], 3)
    assert _stored_pages() == []  # the page waits for the photo
    down["on"] = False
    (photo,) = wp.find_photos(
        [_result(1)], SUBJECT, grok, [], 3
    )  # a "yes" rules nothing out
    assert photo["page_sha256"] and len(grok.vision_asked) == 1


def test_an_unsure_is_not_taken(web):
    images, _ = web
    images["https://img.example.org/sherman1.png"] = _png()
    wp.find_photos([_result(1)], SUBJECT, _Grok(vision=[{"relevant": "unsure"}]), [], 3)
    decided = wp.decision_for(
        "https://img.example.org/sherman1.png", SUBJECT.entity_key
    )
    assert decided["verdict"] == "unsure"


@pytest.mark.parametrize(
    "folder,ids,want",
    [
        ("people", {"PersonID": "01HP"}, "person#01HP"),
        ("places", {"PlaceID": "01HL"}, "place#01HL"),
        ("people_groups", {"PeopleGroupID": "01HG"}, "people_group#01HG"),
        ("equipment", {"EquipmentID": "01HE"}, "equipment#01HE"),
    ],
)
def test_the_retract_script_keys_match_the_finders(tmp_path, folder, ids, want):
    from scripts import retract_photo
    from src.utils.find_queue import entity_origin

    path = tmp_path / folder / "x.json"
    assert retract_photo._subject(path, ids).entity_key == want
    kind = retract_photo.ENTITIES[folder]
    if kind != "equipment":  # equipment queues no finds
        origin = entity_origin(kind, ids)
        assert f"{origin.entity_type}#{origin.entity_id}" == want


def test_a_refused_record_write_rules_nothing_out(tmp_path, monkeypatch):
    import json as _json

    from scripts import retract_photo

    d = tmp_path / "output" / "equipment"
    d.mkdir(parents=True)
    f = d / "x.json"
    f.write_text(
        _json.dumps(
            {"EquipmentID": "01HE", "images": [{"url": "https://i.example/a.png"}]}
        )
    )
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    args = ["--file", str(f), "--url", "https://i.example/a.png", "--reason", "r"]
    assert retract_photo.main(args) == 1
    assert wp.decision_for("https://i.example/a.png", "equipment#01HE") is None


def test_record_only_rules_a_url_out_without_touching_the_record(tmp_path):
    import json as _json

    from scripts import retract_photo

    d = tmp_path / "output" / "equipment"
    d.mkdir(parents=True)
    f = d / "x.json"
    f.write_text(_json.dumps({"EquipmentID": "01HE", "images": []}))
    args = ["--file", str(f), "--url", "https://i.example/a.png", "--reason", "r"]
    assert retract_photo.main(args + ["--record-only"]) == 0
    assert (
        wp.decision_for("https://i.example/a.png", "equipment#01HE")["verdict"]
        == "retracted"
    )


def test_a_url_unusable_for_everyone_is_looked_at_again_after_a_month(web, monkeypatch):
    from src.utils import search_cache

    kept = {}
    real = search_cache.cache_result

    def spy(source, query, result, negative_days=None, positive_days=None):
        kept[(source, query)] = positive_days
        return real(source, query, result, negative_days, positive_days)

    monkeypatch.setattr(search_cache, "cache_result", spy)
    monkeypatch.setattr(
        "src.extraction.images.fetch_image_bytes_with_reason",
        lambda u: (None, None, "not_an_image"),  # a hotlink page served to us, perhaps
    )
    wp.find_photos([_result(1)], SUBJECT, _Grok(), [], 3)
    key = (
        wp._DECISION_SOURCE,
        wp._decision_key("https://img.example.org/sherman1.png", ""),
    )
    assert kept[key] == wp.UNUSABLE_DAYS


# --- an infected photo is deleted, and its record entry dropped (owner 2026-11-23) ------------


class _AvS3:
    def __init__(self, present=()):
        self.objects, self.deleted, self.puts = set(present), [], {}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.discard(Key)

    def put_object(self, Bucket, Key, Body):
        self.puts[Key] = Body
        self.objects.add(Key)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)


def _infected_result(key):
    from src.ingestion import av_scan

    return av_scan.ScanResult(key, av_scan.Verdict.INFECTED, signature="Eicar-Test")


def test_an_infected_fetched_photo_is_deleted_not_quarantined(monkeypatch):
    from src.ingestion import av_scan

    alerts = []
    monkeypatch.setattr(
        "src.utils.observe.publish_alert", lambda s, m, **k: alerts.append(s) or True
    )
    s3 = _AvS3({"unscanned/output/photos/files/abc.png"})
    out = av_scan.AvOutcome()
    av_scan._disposition_staged(
        _infected_result("unscanned/output/photos/files/abc.png"),
        out,
        s3,
        "b",
        "us-east-1",
    )
    assert s3.deleted == ["unscanned/output/photos/files/abc.png"]
    assert not any(k.startswith("quarantine/") for k in s3.puts)  # deleted, not kept
    assert av_scan.was_infected(s3, "b", "output/photos/files/abc.png")
    assert alerts and "deleted" in alerts[0]


def test_an_infected_upload_is_still_quarantined_and_reviewed(monkeypatch):
    from src.ingestion import av_scan

    moved, reviewed = [], []
    monkeypatch.setattr(
        av_scan, "_move_key", lambda c, b, src, dst: moved.append(dst) or True
    )
    monkeypatch.setattr(av_scan, "_submitter_identity", lambda *a: "")
    monkeypatch.setattr(
        av_scan, "reject_to_review", lambda *a, **k: reviewed.append(a[2])
    )
    monkeypatch.setattr(av_scan, "freeze_submitter", lambda *a, **k: None)
    key = "unscanned/contentrepository/user-x/book.pdf"
    av_scan._disposition_staged(
        _infected_result(key), av_scan.AvOutcome(), _AvS3(), "b", ""
    )
    assert moved == ["quarantine/contentrepository/user-x/book.pdf"] and reviewed == [
        key
    ]


def test_a_record_drops_a_photo_clamav_deleted_and_nobody_takes_it_again(monkeypatch):
    from src.ingestion import av_scan

    s3 = _AvS3({av_scan.infected_marker("output/photos/files/bad.png")})
    monkeypatch.setattr(wp, "_s3_and_bucket", lambda: (s3, "b"))
    images = [
        {
            "url": "https://img.example.org/bad.png",
            "local_path": "output/photos/files/bad.png",
        },
        {
            "url": "https://img.example.org/ok.png",
            "local_path": "output/photos/files/ok.png",
        },
        {"url": "https://wiki.example.org/w.jpg"},  # no stored file: not checked
    ]
    assert wp.drop_infected(images) == 1
    assert [i["url"] for i in images] == [
        "https://img.example.org/ok.png",
        "https://wiki.example.org/w.jpg",
    ]
    other = wp.Subject("person#01HP", "c", "s", "w")
    assert wp._ruled_out("https://img.example.org/bad.png", other)  # for everyone


def test_the_phase3_sweep_drops_infected_photos_from_every_entity(
    monkeypatch, tmp_path
):
    import json as _json

    from src.ingestion import av_scan

    s3 = _AvS3({av_scan.infected_marker("output/photos/files/bad.png")})
    monkeypatch.setattr(wp, "_s3_and_bucket", lambda: (s3, "b"))
    bad = {
        "url": "https://i.example/bad.png",
        "local_path": "output/photos/files/bad.png",
    }
    ok = {"url": "https://i.example/ok.png", "local_path": "output/photos/files/ok.png"}
    for entity, idf in (("people", "PersonID"), ("places", "PlaceID"),
                        ("people_groups", "GroupID"), ("equipment", "EquipmentID")):  # fmt: skip
        (tmp_path / entity).mkdir()
        rec = {idf: "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "x", "images": [bad, ok]}
        (tmp_path / entity / "r.json").write_text(_json.dumps(rec))
    writes = []
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock",
        lambda f, d, entity=None: writes.append(
            (entity, [i["url"] for i in d["images"]])
        )
        or True,
    )
    assert wp.drop_infected_photos(tmp_path) == 4
    assert sorted(e for e, _ in writes) == [
        "equipment",
        "people",
        "people_groups",
        "places",
    ]
    assert all(urls == ["https://i.example/ok.png"] for _, urls in writes)


def test_phase3_runs_the_sweep_every_time():
    text = Path("phase3_enrich_data.py").read_text()
    assert '_run_step("infected_photos"' in text


def test_without_a_bucket_nothing_is_checked():
    assert (
        wp.drop_infected([{"url": "u", "local_path": "output/photos/files/x.png"}]) == 0
    )


def test_the_marker_is_written_before_the_delete(monkeypatch):
    from src.ingestion import av_scan

    order = []

    class _S3:
        def put_object(self, Bucket, Key, Body):
            order.append("mark")

        def delete_object(self, Bucket, Key):
            order.append("delete")

    monkeypatch.setattr("src.utils.observe.publish_alert", lambda *a, **k: True)
    av_scan._disposition_staged(
        _infected_result("unscanned/output/photos/files/a.png"),
        av_scan.AvOutcome(),
        _S3(),
        "b",
        "",
    )
    assert order == ["mark", "delete"]


def test_a_failed_delete_is_said_in_the_alert(monkeypatch, caplog):
    import logging

    from src.ingestion import av_scan

    alerts = []
    monkeypatch.setattr(
        "src.utils.observe.publish_alert", lambda s, m, **k: alerts.append(m) or True
    )

    class _S3(_AvS3):
        def delete_object(self, Bucket, Key):
            raise RuntimeError("AccessDenied")

    with caplog.at_level(logging.ERROR):
        av_scan._disposition_staged(
            _infected_result("unscanned/output/maps/files/m.jpg"),
            av_scan.AvOutcome(),
            _S3(),
            "b",
            "",
        )
    assert "OBSERV av.infected_delete_failed" in caplog.text
    assert "DELETE FAILED" in alerts[0] and "NOT cleaned up automatically" in alerts[0]


@pytest.mark.parametrize(
    "key",
    [
        "unscanned/output/content/Book/fig.png",  # derived from an upload
        "unscanned/ocr-output/Book/page.png",
        "unscanned/contentrepository/user-x/book.pdf",
    ],
)
def test_anything_not_fetched_from_the_web_is_quarantined_not_deleted(monkeypatch, key):
    from src.ingestion import av_scan

    moved = []
    monkeypatch.setattr(
        av_scan, "_move_key", lambda c, b, s, d: moved.append(d) or True
    )
    monkeypatch.setattr(av_scan, "_submitter_identity", lambda *a: "")
    monkeypatch.setattr(av_scan, "reject_to_review", lambda *a, **k: None)
    monkeypatch.setattr(av_scan, "freeze_submitter", lambda *a, **k: None)
    s3 = _AvS3({key})
    av_scan._disposition_staged(_infected_result(key), av_scan.AvOutcome(), s3, "b", "")
    assert moved and moved[0].startswith("quarantine/") and s3.deleted == []


def test_an_unreadable_marker_is_said_not_silent(caplog):
    import logging

    from src.ingestion import av_scan
    from src.utils import observe

    observe._counts.clear()

    class _Denied(Exception):
        response = {"Error": {"Code": "AccessDenied"}}

    class _S3:
        def head_object(self, Bucket, Key):
            raise _Denied()

    with caplog.at_level(logging.WARNING):
        assert av_scan.was_infected(_S3(), "b", "output/photos/files/a.png") is False
    assert "infected_check_failed" in caplog.text


def test_a_not_found_marker_is_quiet(caplog):
    import logging

    from src.ingestion import av_scan

    class _Missing(Exception):
        response = {"Error": {"Code": "404"}}

    class _S3:
        def head_object(self, Bucket, Key):
            raise _Missing()

    with caplog.at_level(logging.WARNING):
        assert av_scan.was_infected(_S3(), "b", "output/photos/files/a.png") is False
    assert "infected_check_failed" not in caplog.text


# --- step 2: people, places and groups get vision-confirmed photos too -------------------------


def _capture(monkeypatch):
    seen = {}

    def fake(subject, queries, url, grok, existing, wanted):
        seen.update(
            key=subject.entity_key,
            wanted=wanted,
            queries=list(queries),
            what=subject.what_counts,
        )
        return [_photo_entry()]

    monkeypatch.setattr(oe, "find_entity_photos", fake)
    return seen


def _photo_entry():
    return {
        "url": "https://img.example.org/p.jpg",
        "source": "openserp",
        "candidate_source": "openserp",
        "caption": "c",
        "source_page_url": PAGE,
        "page_sha256": "sha256:" + "a" * 64,
        "sha256": "sha256:" + "b" * 64,
        "phash": "ffff0000ffff0000",
        "local_path": "output/photos/files/b.jpg",
        "vision_check": "verified",
        "vision_verified": True,
        "vision_reason": "r",
        "vision_checked_at": "2026-11-23T00:00:00+00:00",
        "period_photo": "yes",
        "copyright_status": "assumed_public_domain",
        "candidate_added_at": "2026-11-23T00:00:00+00:00",
    }


def _valid(entity, record):
    import jsonschema

    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    spec = next(x for x in ENTITY_REGISTRY if x.name == entity)
    jsonschema.validate(record["images"], load_schema(spec)["properties"]["images"])


def test_a_person_gets_portraits_from_the_image_search(monkeypatch):
    seen = _capture(monkeypatch)
    data = {
        "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "name": "John Doe",
        "images": [{"url": "w"}],
    }
    assert oe._apply_person_photos({"openserp_url": "u"}, data, "John Doe", object(), 3)
    assert seen["key"] == "person#01HX7YZABCDEFGHJKMNPQRSTVW" and seen["wanted"] == 2
    assert seen["queries"] and all("John Doe" in q for q in seen["queries"])
    assert "not someone else of the same name" in seen["what"]
    _valid("people", data)


def test_a_person_with_a_full_record_is_not_photo_searched(monkeypatch):
    seen = _capture(monkeypatch)
    data = {
        "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "images": [{"url": str(i)} for i in range(3)],
    }
    assert (
        oe._apply_person_photos({"openserp_url": "u"}, data, "J", object(), 3) is False
    )
    assert seen == {}


def test_a_place_gets_photos_for_every_name_variant(monkeypatch):
    seen = _capture(monkeypatch)
    data = {"PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW", "current_name": "Metz"}
    assert oe._enrich_place_images(data, ["Metz", "Mes"], "Metz", "u", object(), 3)
    assert seen["key"] == "place#01HX7YZABCDEFGHJKMNPQRSTVW" and seen["wanted"] == 3
    assert any("Mes" in q for q in seen["queries"]) and all(
        "WWII" in q or "1944" in q for q in seen["queries"]
    )  # WWII-scoped queries
    _valid("places", data)


def test_a_group_gets_photos_of_its_soldiers_and_insignia(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setattr(oe, "_entity_caps", lambda i=None, w=None: (3, 3))
    data = {"GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW", "group_name": "3rd Armored"}
    origin = oe.find_queue.entity_origin("people_group", data)
    assert oe._apply_group_photos(data, "3rd Armored", "US", "u", object(), origin)
    assert seen["key"] == "people_group#01HX7YZABCDEFGHJKMNPQRSTVW"
    assert any("insignia" in q.lower() for q in seen["queries"])
    _valid("people_groups", data)


def test_records_without_an_id_get_no_photos(monkeypatch):
    seen = _capture(monkeypatch)
    assert (
        oe._apply_person_photos({"openserp_url": "u"}, {"name": "X"}, "X", object(), 3)
        is False
    )
    assert (
        oe._enrich_place_images(
            {"current_name": "Metz"}, ["Metz"], "Metz", "u", None, 3
        )
        is False
    )
    assert seen == {}


def test_find_entity_photos_stops_sending_queries_once_it_has_enough(monkeypatch):
    sent = []
    monkeypatch.setattr(
        oe, "_search_openserp_images", lambda q, u: sent.append(q) or [_result(1)]
    )
    monkeypatch.setattr(
        wp, "find_photos", lambda r, s, g, e, wanted, **k: [_photo_entry()] * wanted
    )
    out = oe.find_entity_photos(SUBJECT, iter(["q1", "q2", "q3"]), "u", object(), [], 2)
    assert len(out) == 2 and sent == ["q1"]


def test_photos_from_hyperwar_pages_are_skipped(monkeypatch):
    got = []
    monkeypatch.setattr(
        oe,
        "_search_openserp_images",
        lambda q, u: [
            {
                "url": "https://cdn.example/a.jpg",
                "page_url": "https://www.ibiblio.org/hyperwar/x",
            },
            {"url": "https://cdn.example/b.jpg", "page_url": PAGE},
        ],
    )
    monkeypatch.setattr(
        wp, "find_photos", lambda r, *a, **k: got.extend(x["url"] for x in r) or []
    )
    oe.find_entity_photos(SUBJECT, ["q"], "u", object(), [], 3)
    assert got == ["https://cdn.example/b.jpg"]  # HyperWar is ingested as a source


# --- the per-entity budget of photo checks ------------------------------------------------------


def test_the_budget_stops_the_checks_whatever_the_verdicts(web):
    images, fetched = web
    for n in range(1, 6):
        images[f"https://img.example.org/sherman{n}.png"] = _pattern(n)
    grok = _Grok(vision=[{"relevant": "no"}] * 5)
    budget = wp.Budget(2)
    results = [_result(n) for n in range(1, 6)]
    assert wp.find_photos(results, SUBJECT, grok, [], 3, budget=budget) == []
    assert len(fetched) == 2 and len(grok.vision_asked) == 2  # rejections count
    assert budget.spent()
    assert wp.find_photos(results, SUBJECT, grok, [], 3, budget=budget) == []
    assert len(fetched) == 2  # a spent budget downloads nothing more


def test_the_budget_is_shared_by_every_query_of_an_entity(monkeypatch):
    sent, checks = [], []
    monkeypatch.setattr(
        oe, "_search_openserp_images", lambda q, u: sent.append(q) or [_result(1)]
    )

    def find(results, subject, grok, existing, wanted, budget=None):
        for _ in range(3):  # three rejected candidates per query
            budget.spend()
            checks.append(1)
        return []

    monkeypatch.setattr(wp, "find_photos", find)
    monkeypatch.setattr(oe, "_photo_check_budget", lambda: 5)
    oe.reset_metrics()
    out = oe.find_entity_photos(SUBJECT, iter(["q1", "q2", "q3"]), "u", object(), [], 2)
    assert out == [] and sent == ["q1", "q2"]  # q3 is never sent
    assert oe.get_metrics()["photos_budget_spent"] == 1
    oe.reset_metrics()


def test_the_budget_comes_from_config_and_an_out_of_range_value_is_the_default(
    monkeypatch,
):
    for cfg, want in (({}, 10), ({"max_photo_checks_per_entity": 4}, 4),
                      ({"max_photo_checks_per_entity": 10_000}, 10)):  # fmt: skip
        monkeypatch.setattr(
            "src.utils.config.load_config", lambda *a, _c=cfg, **k: {"openserp": _c}
        )
        assert oe._photo_check_budget() == want


def test_a_place_photo_search_uses_its_first_names_only(monkeypatch):
    sent = []
    monkeypatch.setattr(
        oe, "find_entity_photos", lambda s, queries, *a: sent.extend(queries) or []
    )
    names = ["Metz", "Mes", "Metzig", "Divodurum", "Mettis"]
    oe._enrich_place_images({"PlaceID": "01HPL"}, names, "Metz", "u", object(), 3)
    assert sent and not any(("Divodurum" in q or "Mettis" in q) for q in sent)
    assert any("Metzig" in q for q in sent)


def test_a_group_without_an_id_is_not_searched(monkeypatch):
    monkeypatch.setattr(oe, "_search_openserp", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(oe, "find_entity_photos", lambda *a, **k: 1 / 0)
    data = {"group_name": "101st Airborne Division", "nationality": "USA"}
    assert (
        oe._apply_group_categories(
            data, "101st Airborne Division", "USA", "u", object()
        )
        is False
    )


def test_a_sweep_write_the_guard_blocks_is_reported_not_counted(
    monkeypatch, tmp_path, caplog
):
    import json as _json

    from src.ingestion import av_scan

    s3 = _AvS3({av_scan.infected_marker("output/photos/files/bad.png")})
    monkeypatch.setattr(wp, "_s3_and_bucket", lambda: (s3, "b"))
    (tmp_path / "people").mkdir()
    rec = {"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "x",
           "images": [{"url": "https://i.example/bad.png", "local_path": "output/photos/files/bad.png"}]}  # fmt: skip
    (tmp_path / "people" / "r.json").write_text(_json.dumps(rec))
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda f, d, entity=None: False
    )
    oe.reset_metrics()
    with caplog.at_level(logging.ERROR):
        assert wp.drop_infected_photos(tmp_path) == 0
    assert (
        "OBSERV web_photos.infected_write_blocked record=r.json photos=1" in caplog.text
    )
    assert oe.get_metrics()["photos_infected_write_blocked"] == 1
    oe.reset_metrics()


def test_a_record_without_a_stored_photo_is_not_parsed(tmp_path):
    f = tmp_path / "r.json"
    f.write_text("[" * 100_000)  # would blow the parser's recursion limit
    assert wp._with_stored_photos(f) is None
    f.write_text('{"images": [{"local_path": "x"}]' + "[" * 100_000)
    assert wp._with_stored_photos(f) is None  # unparseable: skipped, not raised


def test_a_budget_spent_on_the_last_query_is_still_counted(monkeypatch):
    monkeypatch.setattr(oe, "_search_openserp_images", lambda q, u: [_result(1)])

    def find(results, subject, grok, existing, wanted, budget=None):
        budget.spend()
        return []

    monkeypatch.setattr(wp, "find_photos", find)
    monkeypatch.setattr(oe, "_photo_check_budget", lambda: 1)
    oe.reset_metrics()
    assert oe.find_entity_photos(SUBJECT, ["only"], "u", object(), [], 2) == []
    assert oe.get_metrics()["photos_budget_spent"] == 1
    oe.reset_metrics()
