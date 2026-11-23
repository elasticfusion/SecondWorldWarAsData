"""Web photos (owner 2026-11-23): an OpenSERP image search, a batched text pre-filter, the photo and
its page downloaded and kept (page without active content, photo in the AV staging area), the same
bytes never judged twice for an entity, and every kept photo confirmed by Grok vision.
"""

import io
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


def test_the_photo_waits_in_the_av_staging_area():
    assert find_pages.PHOTO_STAGING_PREFIX == "unscanned/output/photos/files"
    from src.ingestion.av_scan import UNSCANNED_PREFIX, _prod_path

    staged = f"{find_pages.PHOTO_STAGING_PREFIX}/abc.png"
    assert staged.startswith(UNSCANNED_PREFIX)
    assert (
        _prod_path(staged) == "output/photos/files/abc.png"
    )  # what a clean scan promotes to


def test_in_aws_a_photo_is_written_only_to_the_staging_area(monkeypatch):
    from src.utils.storage import S3Storage

    monkeypatch.setattr(find_pages, "_bucket", lambda: "dev-wwii-data-pipeline")
    store = find_pages._storage(
        find_pages.PHOTO_STAGING_PREFIX, find_pages._PHOTO_LOCAL_DIR
    )
    assert isinstance(store, S3Storage)
    assert store._key("x.png") == "unscanned/output/photos/files/x.png"


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

    def find(results, subject, grok, existing, wanted):
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
