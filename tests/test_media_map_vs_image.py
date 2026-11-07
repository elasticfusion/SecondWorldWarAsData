"""Map-vs-image classification end to end (XChannel-8 E2E regression, 2026-11-06).

ibiblio: the URL path decides (maps/ -> map, img/ + icn/ -> image). Other hosts: Grok vision,
deferred to Phase 3 but never skipped. Covers the parser (thumbnails -> hi-res, guard alert),
Phase 2 image stamping, Phase 3 pending resolution (map persisted, provenance both ways),
Phase 0 source_url resolution, and the Phase 2/3 entity-dir sync list.
"""

import io
import json
from types import SimpleNamespace

import pytest

import src.extraction.image_map_vision as imv
import src.extraction.media_classify as mc
from src.extraction.media_classify import classify_media, vision_is_map

IB = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel"
PAGE = f"{IB}/USA-E-XChannel-8.html"

# The three HyperWar forms seen in the chapter (pandoc passes the raw anchors through).
HYPERWAR_MD = """\
![](img/USA-E-XChannel.jpg)

> <a href="maps/USA-E-XChannel-1.jpg" target="mapwindow"><img src="maps/USA-E-XChannel-1m.jpg" /></a>
> *Map 1*

> He moved east. (See <a href="maps/USA-E-XChannel-VII.jpg" target="mapwindow"><em>Map VII</em></a>.)

> Cole dropped east. (See <a href="maps/USA-E-XChannel-VIII.jpg" target="mapwindow"><em>Map VIII</em></a>.)

> <a href="img/USA-E-XChannel-40.jpg" target="mapwindow"><img src="icn/USA-E-XChannel-40.jpg" /></a>

> <a href="img/USA-E-XChannel-42.jpg" target="mapwindow"><img src="icn/USA-E-XChannel-42.jpg" /></a>
"""


# --- shared classifier ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,kind",
    [
        (f"{IB}/maps/USA-E-XChannel-1.jpg", "map"),
        (f"{IB}/maps/USA-E-XChannel-VII.jpg", "map"),
        (f"{IB}/maps/USA-E-XChannel-VIII.jpg", "map"),
        (f"{IB}/img/USA-E-XChannel-40.jpg", "image"),
        (f"{IB}/img/USA-E-XChannel-42.jpg", "image"),
        (f"{IB}/icn/USA-E-XChannel-42.jpg", "image"),
        ("https://example.org/maps/m8.jpg", "ambiguous"),  # non-ibiblio: vision decides
        ("https://nara.gov/img/photo.jpg", "ambiguous"),
    ],
)
def test_classify_media_url_rule(url, kind):
    assert classify_media("", url).kind == kind


def test_charts_are_diagram_images():
    v = classify_media("", f"{IB}/charts/USA-E-XChannel-1.jpg")
    assert (v.kind, v.content_type) == ("image", "diagram")


class _FakeGrok:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    def extract_json_with_image_base64(self, **_k):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def test_vision_is_map_decides_or_returns_none():
    assert vision_is_map(None, {}, _FakeGrok())[0] is None  # no bytes
    assert vision_is_map(b"x", {}, None)[0] is None  # no client
    assert vision_is_map(b"x", {}, _FakeGrok(exc=RuntimeError()))[0] is None  # error
    unk = _FakeGrok({"classification": "unknown", "description": "", "confidence": 0})
    assert vision_is_map(b"x", {}, unk)[0] is None
    m = _FakeGrok({"classification": "map", "description": "a map", "confidence": 0.9})
    assert vision_is_map(b"x", {}, m)[0] is True
    p = _FakeGrok(
        {"classification": "photograph", "description": "men", "confidence": 0.9}
    )
    assert vision_is_map(b"x", {}, p)[0] is False
    # Case-insensitive; an unrecognised answer is UNDECIDED, never a silent "not a map".
    cap = _FakeGrok({"classification": "Map", "description": "m", "confidence": 0.9})
    assert vision_is_map(b"x", {}, cap)[0] is True
    odd = _FakeGrok(
        {"classification": "illustration", "description": "", "confidence": 1}
    )
    assert vision_is_map(b"x", {}, odd)[0] is None


# --- Phase 1 parser -------------------------------------------------------------------------


def test_parser_maps_and_images_resolve_to_hi_res():
    from src.parser import extract_images, extract_maps

    maps = [u for _id, u in extract_maps(HYPERWAR_MD, base_url=PAGE)]
    for n in ("1", "VII", "VIII"):
        assert f"{IB}/maps/USA-E-XChannel-{n}.jpg" in maps
    assert not any(u.endswith("1m.jpg") for u in maps)  # thumbnail -> hi-res href
    imgs = [u for _k, u, _a, _e in extract_images(HYPERWAR_MD, base_url=PAGE)]
    for n in ("40", "42"):
        assert f"{IB}/img/USA-E-XChannel-{n}.jpg" in imgs
    assert not any("/icn/" in u for u in imgs)  # no thumbnail duplicates


def test_parser_guard_alerts_when_base_url_is_not_http(monkeypatch):
    import src.parser as parser
    import src.utils.observe as obs

    alerts = []
    obs.reset_counted_warnings()
    monkeypatch.setattr(
        obs, "observe_warn", lambda *a, **k: alerts.append((a, k)) or ""
    )
    assert parser._warn_unresolvable_media(HYPERWAR_MD, PAGE, "B") == 0
    assert not alerts
    lost = parser._warn_unresolvable_media(HYPERWAR_MD, "converted from x.html", "B")
    parser._warn_unresolvable_media(HYPERWAR_MD, "", "B")  # 2nd section, same book
    assert lost == 6  # header, maps 1/VII/VIII (hi-res), photos 40/42 — deduped
    assert len(alerts) == 1 and alerts[0][0][1] == "media_unresolvable"  # once per book
    assert alerts[0][1]["note"]
    # Pandoc-extracted EPUB/DOCX media has no URL to supply: not counted, no alert.
    assert parser._warn_unresolvable_media("![](media/image1.png)", "", "E") == 0


# --- Phase 2 image stamping -----------------------------------------------------------------


def _run_phase2_images(tmp_path, monkeypatch, urls, map_urls=()):
    from src.extraction import images

    registered = []
    import src.extraction.batch_parallel as bp

    monkeypatch.setattr(bp, "_register_entity", lambda *a: registered.append(a))
    doc = tmp_path / "content" / "B"
    doc.mkdir(parents=True)
    (doc / "chapter1-parsed.json").write_text(
        json.dumps(
            {
                "book": "B",
                "images": [
                    {
                        "type": "external",
                        "url": u,
                        "alt_text": "",
                        "paragraph_number": 0,
                    }
                    for u in urls
                ],
                "maps": [{"url": u} for u in map_urls],
            }
        )
    )
    images.extract_images(
        tmp_path,
        tmp_path / "places",
        tmp_path / "dates",
        download=False,
        captioner=None,
    )
    recs = [
        json.loads(p.read_text())
        for p in (tmp_path / "images").glob("*.json")
        if p.name != "index.json"
    ]
    return {r["url"]: r for r in recs}, registered


def test_phase2_stamps_map_check_and_skips_ibiblio_maps(tmp_path, monkeypatch):
    other = "https://example.org/fig/f1.jpg"
    recs, registered = _run_phase2_images(
        tmp_path,
        monkeypatch,
        [f"{IB}/maps/USA-E-XChannel-1.jpg", f"{IB}/img/USA-E-XChannel-40.jpg", other],
        map_urls=[f"{IB}/maps/USA-E-XChannel-1.jpg"],
    )
    assert f"{IB}/maps/USA-E-XChannel-1.jpg" not in recs  # Phase 3 owns ibiblio maps
    ib = recs[f"{IB}/img/USA-E-XChannel-40.jpg"]
    assert (ib["map_check"], ib["is_map"], ib["map_check_basis"]) == (
        "not_map",
        False,
        "ibiblio_url",
    )
    assert (
        recs[other]["map_check"] == "pending_vision" and recs[other]["is_map"] is None
    )
    assert {r[1] for r in registered} == {"images"} and len(registered) == 2


# --- Phase 3 deferred map vision ------------------------------------------------------------


def _pending_image(tmp_path, url="https://example.org/fig/f1.jpg"):
    d = tmp_path / "images"
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "ImageID": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "image_title": "Figure 1",
        "url": url,
        "source": "Original book illustration - B",
        "map_check": "pending_vision",
        "is_map": None,
        "description": "",
    }
    f = d / f"{rec['ImageID']}.json"
    f.write_text(json.dumps(rec))
    return f


def _cap(cls):
    return SimpleNamespace(classification=cls, description=f"a {cls}")


def test_phase3_vision_map_persists_map_with_provenance(tmp_path, monkeypatch):
    f = _pending_image(tmp_path)
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: b"img")
    monkeypatch.setattr(imv, "vision_is_map", lambda b, c, g: (True, _cap("map")))
    counts = imv.resolve_pending(tmp_path, object(), download=False)
    assert counts["map"] == 1 and counts["undecided"] == 0
    rec = json.loads(f.read_text())
    assert rec["map_check"] == "map" and rec["content_type"] == "map"
    map_file = tmp_path / "maps" / f"{rec['superseded_by_map_id']}.json"
    m = json.loads(map_file.read_text())
    assert m["source_url"] == rec["url"]  # provenance back to the source image URL
    assert (
        m["is_map"] is True and m["map_vision_checked_at"]
    )  # not re-submitted to Grok


def test_phase3_vision_not_map(tmp_path, monkeypatch):
    f = _pending_image(tmp_path)
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: b"img")
    monkeypatch.setattr(
        imv, "vision_is_map", lambda b, c, g: (False, _cap("photograph"))
    )
    assert imv.resolve_pending(tmp_path, object())["not_map"] == 1
    rec = json.loads(f.read_text())
    assert rec["map_check"] == "not_map" and rec["description"] == "a photograph"
    assert not (tmp_path / "maps").exists() or not list(
        (tmp_path / "maps").glob("0*.json")
    )


def test_phase3_undecided_stays_pending_and_alerts(tmp_path, monkeypatch):
    f = _pending_image(tmp_path)
    alerts = []
    monkeypatch.setattr(imv, "observe_warn", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: None)
    counts = imv.resolve_pending(tmp_path, None)
    assert counts["undecided"] == 1
    assert (
        json.loads(f.read_text())["map_check"] == "pending_vision"
    )  # retried next run
    assert (
        alerts and alerts[0][0][1] == "map_vision_incomplete" and alerts[0][1]["note"]
    )


def test_phase3_image_bytes_only_reads_images_files(tmp_path):
    out = tmp_path / "out"
    (out / "people").mkdir(parents=True)
    (out / "people" / "p.json").write_bytes(b"{}")
    (tmp_path / "secret").write_bytes(b"x")
    for local in ("../secret", "people/p.json", "/etc/passwd"):
        assert imv._image_bytes({"local_copy": local, "url": ""}, out) is None
    (out / "images" / "files").mkdir(parents=True)
    (out / "images" / "files" / "a.jpg").write_bytes(b"jpg")
    assert imv._image_bytes({"local_copy": "images/files/a.jpg"}, out) == b"jpg"


def test_phase3_reuses_phase2_vision_verdict(tmp_path, monkeypatch):
    f = _pending_image(tmp_path)
    rec = json.loads(f.read_text())
    rec.update(caption_method="grok-vision", vision_classification="photograph")
    f.write_text(json.dumps(rec))

    def _no_call(*_a):
        pytest.fail("must not call Grok again")

    monkeypatch.setattr(imv, "vision_is_map", _no_call)
    assert imv.resolve_pending(tmp_path, object())["not_map"] == 1


def test_phase3_refused_write_is_undecided_and_alerts(tmp_path, monkeypatch):
    import src.utils.file_lock as fl

    _pending_image(tmp_path)
    alerts = []
    monkeypatch.setattr(imv, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: b"img")
    monkeypatch.setattr(
        imv, "vision_is_map", lambda b, c, g: (False, _cap("photograph"))
    )
    monkeypatch.setattr(fl, "write_json_with_lock", lambda *a, **k: False)
    assert imv.resolve_pending(tmp_path, object())["undecided"] == 1
    assert alerts == ["map_vision_incomplete"]


def test_orphan_ibiblio_map_recorded_then_persisted_without_vision(
    tmp_path, monkeypatch
):
    """A bare ibiblio maps/ image the parser did not list in maps[] must not be dropped."""
    orphan = f"{IB}/maps/USA-E-XChannel-99.jpg"
    recs, _ = _run_phase2_images(tmp_path, monkeypatch, [orphan])
    assert recs[orphan]["map_check"] == "map" and recs[orphan]["is_map"] is True

    def _no_call(*_a):
        pytest.fail("no vision for ibiblio")

    monkeypatch.setattr(imv, "vision_is_map", _no_call)
    assert imv.resolve_pending(tmp_path, None, download=False)["map"] == 1
    rec = json.loads(
        next(
            p for p in (tmp_path / "images").glob("*.json") if p.name != "index.json"
        ).read_text()
    )
    assert rec["superseded_by_map_id"]


# --- Phase 0 source_url ----------------------------------------------------------------------


class _S3:
    def __init__(self, metadata=None, head=b""):
        self.metadata, self.head = metadata or {}, head

    def head_object(self, **_k):
        return {"Metadata": self.metadata}

    def get_object(self, **_k):
        return {"Body": io.BytesIO(self.head)}


def test_source_url_from_metadata_is_cleaned(monkeypatch):
    from src.ingestion.source_url import resolve_source_url

    s3 = _S3({"source-url": PAGE + "#top"})
    assert resolve_source_url(s3, "b", "k/x.html", "USA-E-XChannel-8") == (
        PAGE,
        "s3_metadata",
    )


def test_source_url_derived_for_hyperwar_title(monkeypatch):
    import src.ingestion.source_url as su

    alerts = []
    monkeypatch.setattr(su, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    s3 = _S3({}, b"<html><head><title>HyperWar: Cross Channel Invasion</title>")
    assert su.resolve_source_url(s3, "b", "k/x.html", "USA-E-XChannel-8") == (
        PAGE,
        "derived_hyperwar",
    )
    assert alerts == ["source_url_derived"]


def test_source_url_missing_rejects_non_url_and_alerts(monkeypatch):
    import src.ingestion.source_url as su

    alerts = []
    monkeypatch.setattr(su, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    s3 = _S3({"source-url": "converted from k"}, b"<title>Some Other Site</title>")
    assert su.resolve_source_url(s3, "b", "k/x.html", "USA-E-XChannel-8") == (
        "",
        "missing",
    )
    assert alerts == ["source_url_missing"]


def test_source_url_non_web_file_is_not_an_alert(monkeypatch):
    import src.ingestion.source_url as su

    alerts = []
    monkeypatch.setattr(su, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    assert su.resolve_source_url(_S3(), "b", "k/book.epub", "B") == ("", "missing")
    assert not alerts  # EPUB/DOCX have no page URL and lose nothing: record, not alert


def test_meta_yaml_has_real_url_and_source_key():
    import yaml

    import phase0_convert as p0

    meta = yaml.safe_load(
        p0._build_meta_yaml("B", "contentrepository/B/B.html", PAGE, "s3_metadata")
    )
    assert (
        meta["source_url"] == PAGE
        and meta["source_key"] == "contentrepository/B/B.html"
    )
    assert "converted from" not in yaml.safe_dump(meta)


# --- Phase 2/3 sync list ---------------------------------------------------------------------


def test_images_are_downloaded_and_final_synced():
    import os

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ee

    assert "images" in ee._PHASE23_ENTITY_DIRS and "maps" in ee._PHASE23_ENTITY_DIRS


def test_document_media_steps_run_without_openserp(monkeypatch, tmp_path):
    """In-doc maps + deferred map vision must not depend on external_maps.enabled/use_openserp."""
    import phase3_enrich_data as p3

    ran = []
    args = SimpleNamespace(output_dir=tmp_path)
    config = {"external_maps": {"enabled": False}, "supplemental_material": {}}
    p3._run_document_media_steps(
        args, config, None, lambda name, fn: ran.append(name) or 0
    )
    assert ran == ["in_doc_map_links", "image_map_vision"]


def test_in_doc_maps_persist_always_but_recycle_only_when_enabled(
    monkeypatch, tmp_path
):
    """Map ENTITIES always persist; the paid relevance-vision recycle keeps its cost gate."""
    import phase3_enrich_data as p3
    import src.extraction.map_link_source as mls

    calls = []
    monkeypatch.setattr(
        p3, "_load_parsed_docs", lambda out: [{"book": "B", "maps": []}]
    )
    monkeypatch.setattr(
        mls, "persist_map_entities", lambda *a, **k: calls.append("persist")
    )
    monkeypatch.setattr(
        mls, "ingest_in_doc_map_links", lambda *a, **k: calls.append("recycle") or {}
    )
    p3._ingest_in_doc_map_links(tmp_path, None, {"enabled": False})
    assert calls == ["persist"]
    calls.clear()
    p3._ingest_in_doc_map_links(tmp_path, None, {"enabled": True})
    assert calls == ["recycle", "persist"]


def test_all_converters_share_one_meta_builder():
    import inspect

    import lambda_handlers.ocr_merge_handler as ocr
    import phase0_convert as p0
    import phase0_video as vid

    for mod in (p0, vid, ocr):
        src = inspect.getsource(mod)
        assert "build_meta_yaml" in src and "yaml.safe_dump" not in src, mod.__name__
    assert 'f"OCR of {book}"' not in inspect.getsource(ocr)  # old non-URL placeholder


def test_undecided_vision_is_capped_then_handed_to_a_human(tmp_path, monkeypatch):
    f = _pending_image(tmp_path)
    alerts = []
    monkeypatch.setattr(imv, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: b"img")
    calls = {"n": 0}

    def _undecided(*_a):
        calls["n"] += 1
        return None, None

    monkeypatch.setattr(imv, "vision_is_map", _undecided)
    for _ in range(imv.MAX_VISION_ATTEMPTS):
        assert imv.resolve_pending(tmp_path, object())["undecided"] == 1
    out = imv.resolve_pending(tmp_path, object())  # cap reached: no further Grok call
    assert out["needs_human"] == 1 and calls["n"] == imv.MAX_VISION_ATTEMPTS
    rec = json.loads(f.read_text())
    assert rec["needs_review"] is True and rec["map_check"] == "pending_vision"
    assert alerts[-1] == "map_vision_needs_human"


def test_orphan_map_claims_no_vision_timestamp(tmp_path, monkeypatch):
    orphan = f"{IB}/maps/USA-E-XChannel-98.jpg"
    _run_phase2_images(tmp_path, monkeypatch, [orphan])
    imv.resolve_pending(tmp_path, None, download=False)
    m = next(
        json.loads(p.read_text())
        for p in (tmp_path / "maps").glob("*.json")
        if p.name != "index.json"
    )
    assert m["source_url"] == orphan and not m.get("map_vision_checked_at")


def test_map_persist_failure_is_retryable_and_alerts(tmp_path, monkeypatch):
    f = _pending_image(tmp_path, url="not a url")  # clean_url rejects -> no Map
    alerts = []
    monkeypatch.setattr(imv, "observe_warn", lambda *a, **k: alerts.append(a[1]))
    monkeypatch.setattr(imv, "_image_bytes", lambda rec, root: b"img")
    monkeypatch.setattr(imv, "vision_is_map", lambda b, c, g: (True, _cap("map")))
    assert imv.resolve_pending(tmp_path, object())["persist_failed"] == 1
    rec = json.loads(f.read_text())
    assert (
        rec["map_check"] == "pending_vision" and rec["is_map"] is True
    )  # verdict kept
    assert alerts == ["map_persist_failed"]

    def _no_call(*_a):
        pytest.fail("persist retry must not re-ask Grok")

    monkeypatch.setattr(imv, "vision_is_map", _no_call)
    assert imv.resolve_pending(tmp_path, object())["persist_failed"] == 1


def test_link_persisted_map_uses_callers_grok_client(tmp_path, monkeypatch):
    import src.extraction.map_common as mcm
    import src.extraction.map_link_source as mls

    seen = []
    monkeypatch.setattr(
        mls, "_grok_client_for_vision", lambda: pytest.fail("must use caller's client")
    )
    monkeypatch.setattr(
        mcm, "maybe_vision_classify_map", lambda rec, loader, g: seen.append(g)
    )
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps({"MapID": "m", "source_url": "https://x/a.jpg"}))
    client = object()
    mls._link_persisted_map(mf, tmp_path, client)
    assert seen == [client]


def test_in_doc_maps_persist_even_when_follow_links_disabled(monkeypatch, tmp_path):
    """Phase 2 skips listed ibiblio maps as images, so Phase 3 must ALWAYS persist them."""
    import phase3_enrich_data as p3
    import src.extraction.map_link_source as mls

    calls = []
    monkeypatch.setattr(
        p3, "_load_parsed_docs", lambda out: [{"book": "B", "maps": []}]
    )
    monkeypatch.setattr(
        mls, "persist_map_entities", lambda *a, **k: calls.append("persist")
    )
    monkeypatch.setattr(
        mls, "ingest_in_doc_map_links", lambda *a, **k: calls.append("recycle") or {}
    )
    cfg = {"enabled": True, "follow_in_doc_links": False}
    p3._ingest_in_doc_map_links(tmp_path, None, cfg)
    assert calls == ["persist"]
