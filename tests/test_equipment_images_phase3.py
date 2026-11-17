"""Phase 3 equipment images (src/enrichment/equipment_images): specific equipment only, every image
confirmed by Grok vision; generic records cleaned; undecided images retried then needs_review.

XChannel-8 batch E2E (2026-11-07): 169 equipment images attached, 0 vision-checked ("trench" -> the
Mariana Trench, "rifle" -> a benchrest Mauser, "torpedo boat" -> an oil painting).
"""

import json
import logging

import pytest

from src.enrichment import equipment_images as EIM

UL = "01ABCDEFGH0123456789ABCDEF"


def _write(tmp_path, rec, name="e.json"):
    f = tmp_path / name
    f.write_text(json.dumps({"EquipmentID": UL, **rec}), encoding="utf-8")
    return f


def _read(f):
    return json.loads(f.read_text(encoding="utf-8"))


@pytest.fixture
def vision(monkeypatch):
    """Scripted image fetch + vision: verdict per URL (True / False / None = undecided)."""
    verdicts = {}
    calls = []

    monkeypatch.setattr(EIM, "_fetch_bytes", lambda url: url.encode() if url else None)
    import src.extraction.equipment as EQ

    def fake_vision(body, name, category, title, grok):
        url = body.decode()
        calls.append(url)
        v = verdicts.get(url, True)
        return v, "ok" if v else ("no" if v is False else "error")

    monkeypatch.setattr(EQ, "_verify_media_with_vision", fake_vision)
    monkeypatch.setattr(EQ, "_compute_image_hash", lambda body: body.decode())
    monkeypatch.setattr(EIM, "article_image_candidates", lambda title: [])
    return verdicts, calls


def test_generic_record_loses_its_enrichment_and_images(tmp_path, vision):
    f = _write(
        tmp_path,
        {
            "common_name": "trench",
            "identity_scope": "generic",
            "description": "From the narrative.",
            "images": [{"url": "https://x/mariana.jpg"}],
            "media": [{"url": "https://x/mariana.jpg"}],
            "external_data": {
                "wikipedia_url": "https://en.wikipedia.org/wiki/Mariana_Trench"
            },
            "wikipedia_extract": "The Mariana Trench...",
            "enrichment_status": "enriched",
            "timeline": {"first_production": "x", "source": "wikipedia"},
            "event_mentions": [{"MentionID": UL, "EventID": UL}],
        },
    )
    counts = EIM.resolve_equipment_images(tmp_path, grok_client=None)
    rec = _read(f)
    for key in (
        "images",
        "media",
        "external_data",
        "wikipedia_extract",
        "enrichment_status",
        "timeline",
    ):
        assert key not in rec, key
    assert rec["description"] == "From the narrative."  # narrative-sourced fields kept
    assert rec["event_mentions"] and counts["generic_cleaned"] == 1
    assert vision[1] == []  # never vision-checked, never searched


def test_unknown_scope_without_grok_is_left_untouched(tmp_path, vision):
    original = {"common_name": "Mulberry", "images": [{"url": "https://x/m.jpg"}]}
    f = _write(tmp_path, original)
    EIM.resolve_equipment_images(tmp_path, grok_client=None)
    assert _read(f)["images"] == [
        {"url": "https://x/m.jpg"}
    ]  # never stripped on an unknown


def test_legacy_unverified_images_are_queued_and_vision_decides(tmp_path, vision):
    verdicts, calls = vision
    verdicts.update({"https://x/ok.jpg": True, "https://x/ming.jpg": False})
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "identity_scope": "specific",
            "images": [
                {"url": "https://x/ming.jpg", "vision_verified": False},
                {"url": "https://x/ok.jpg", "vision_verified": False},
            ],
            "media": [{"url": "https://x/ming.jpg"}],
        },
    )
    counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    imgs = {i["url"]: i for i in _read(f)["images"]}
    assert imgs["https://x/ming.jpg"]["vision_check"] == "rejected"
    assert imgs["https://x/ming.jpg"]["vision_verified"] is False
    assert imgs["https://x/ok.jpg"]["vision_check"] == "verified"
    assert imgs["https://x/ok.jpg"]["vision_verified"] is True
    assert "media" not in _read(f)  # the unchecked legacy copy is dropped
    assert (counts["verified"], counts["rejected"]) == (1, 1)


def test_candidates_only_from_a_wwii_qualified_article(tmp_path, vision, monkeypatch):
    titles = []
    monkeypatch.setattr(
        EIM,
        "article_image_candidates",
        lambda title: titles.append(title)
        or [{"url": "https://c/1.jpg", "title": "M1"}],
    )
    ok = _write(
        tmp_path,
        {
            "common_name": "M1 Garand",
            "identity_scope": "specific",
            "external_data": {
                "wikipedia_url": "https://en.wikipedia.org/wiki/M1_Garand"
            },
            "wikipedia_wwii_relevant": True,
        },
        "ok.json",
    )
    _write(
        tmp_path,
        {
            "common_name": "M1 Garand",
            "identity_scope": "specific",
            "external_data": {
                "wikipedia_url": "https://en.wikipedia.org/wiki/Benchrest"
            },
            "wikipedia_extract": "Benchrest shooting is a sport.",  # legacy: no WWII in text
        },
        "legacy.json",
    )
    EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert titles == ["M1 Garand"]  # the unqualified legacy article contributed nothing
    rec = _read(ok)
    assert rec["image_candidates_checked_at"]
    (img,) = rec["images"]
    assert img["candidate_source"] == "wikipedia" and img["vision_check"] == "verified"


def test_unreachable_wikipedia_defers_candidates_and_alerts(
    tmp_path, vision, monkeypatch, caplog
):
    """Transient candidates: not stamped (retried next run) and alerted, never treated as 'none'."""
    monkeypatch.setattr(EIM, "article_image_candidates", lambda title: None)
    f = _write(
        tmp_path,
        {
            "common_name": "M1 Garand",
            "identity_scope": "specific",
            "external_data": {
                "wikipedia_url": "https://en.wikipedia.org/wiki/M1_Garand"
            },
            "wikipedia_wwii_relevant": True,
        },
    )
    with caplog.at_level(logging.WARNING):
        counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert counts["candidates_transient"] == 1
    assert "image_candidates_checked_at" not in _read(f)
    assert "equipment_image_candidates_deferred" in caplog.text


def test_wiki_json_unreachable_or_non_dict_is_none(monkeypatch):
    import src.utils.wikimedia_client as WC

    class R:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    for resp, expected in (
        (WC.TRANSIENT, None),
        (R(ValueError("bad")), None),
        (R(["not", "a", "dict"]), None),
        (R({"query": {}}), {"query": {}}),
    ):
        monkeypatch.setattr(WC, "wikimedia_get", lambda *a, _r=resp, **k: _r)
        assert (
            EIM._wiki_json("https://en.wikipedia.org/w/api.php", "wikipedia", {}, "s")
            == expected
        )


def test_undecided_is_retried_then_needs_review(tmp_path, vision, caplog):
    verdicts, _ = vision
    verdicts["https://x/u.jpg"] = None
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "identity_scope": "specific",
            "images": [{"url": "https://x/u.jpg"}],
        },
    )
    for _ in range(EIM.MAX_VISION_ATTEMPTS):
        EIM.resolve_equipment_images(tmp_path, grok_client=object())
    img = _read(f)["images"][0]
    assert (
        img["vision_check"] == "pending"
        and img["vision_attempts"] == EIM.MAX_VISION_ATTEMPTS
    )
    assert img["vision_verified"] is False  # never attached while undecided
    with caplog.at_level(logging.WARNING):
        counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert counts["needs_human"] == 1 and _read(f)["images"][0]["needs_review"] is True
    assert any("equipment_image_needs_human" in r.getMessage() for r in caplog.records)


def test_stops_after_enough_verified_and_drops_duplicates(
    tmp_path, vision, monkeypatch
):
    import src.extraction.equipment as EQ

    _, calls = vision
    monkeypatch.setattr(
        EQ,
        "_compute_image_hash",
        lambda body: "same" if b"dup" in body else body.decode(),
    )
    urls = [
        "https://x/a.jpg",
        "https://x/dup1.jpg",
        "https://x/dup2.jpg",
        "https://x/b.jpg",
        "https://x/c.jpg",
    ]
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "identity_scope": "specific",
            "images": [{"url": u} for u in urls],
        },
    )
    EIM.resolve_equipment_images(tmp_path, grok_client=object(), images_per_record=3)
    imgs = {i["url"]: i["vision_check"] for i in _read(f)["images"]}
    assert (
        imgs["https://x/dup2.jpg"] == "rejected"
    )  # perceptual duplicate of a verified image
    assert list(imgs.values()).count("verified") == 3
    assert imgs["https://x/c.jpg"] == "pending"  # enough verified: not checked this run


def test_unresolved_vessel_gets_no_images(tmp_path, vision, monkeypatch):
    monkeypatch.setattr(
        EIM,
        "article_image_candidates",
        lambda title: (_ for _ in ()).throw(AssertionError),
    )
    f = _write(tmp_path, {"common_name": "Augusta", "identity_scope": "unresolved"})
    EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert "images" not in _read(f)


def test_records_validate_against_the_schema(tmp_path, vision):
    import jsonschema

    from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA

    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "identity_scope": "specific",
            "images": [{"url": "https://x/a.jpg"}],
        },
    )
    EIM.resolve_equipment_images(tmp_path, grok_client=object())
    jsonschema.validate(_read(f), EQUIPMENT_OUTPUT_SCHEMA)


def test_openserp_never_searches_generic_equipment(tmp_path, monkeypatch):
    from src.enrichment import openserp_enrichment as oe

    searched = []
    monkeypatch.setattr(
        oe,
        "enrich_one_equipment",
        lambda data, *a: searched.append(data["common_name"]),
    )
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    for name, scope in (("rifle", "generic"), ("M1 Garand", "specific")):
        f = tmp_path / f"{name}.json"
        f.write_text(
            json.dumps({"common_name": name, "identity_scope": scope}), encoding="utf-8"
        )
        oe._process_equipment_file(f, "http://x", None)
    assert searched == ["M1 Garand"]


# --- review hardening (2026-11-07) ------------------------------------------------------------


def _specific(images, **extra):
    return {
        "common_name": "M1 Garand",
        "identity_scope": "specific",
        "images": images,
        **extra,
    }


def test_legacy_verified_flag_without_a_recorded_check_is_rechecked(tmp_path, vision):
    """Old Phase 2 set vision_verified from the CONFIG flag, not per image: re-verify it."""
    _, calls = vision
    f = _write(
        tmp_path,
        _specific(
            [
                {"url": "https://x/legacy.jpg", "vision_verified": True},
                {
                    "url": "https://x/checked.jpg",
                    "vision_verified": True,
                    "vision_checked_at": "2026-10-01T00:00:00+00:00",
                },
            ]
        ),
    )
    EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert calls == [
        "https://x/legacy.jpg"
    ]  # the recorded check is trusted, the flag is not
    assert all(i["vision_check"] == "verified" for i in _read(f)["images"])


def test_fetch_failures_do_not_spend_the_vision_budget(tmp_path, vision, monkeypatch):
    monkeypatch.setattr(EIM, "_fetch_bytes", lambda url: None)
    f = _write(tmp_path, _specific([{"url": "https://x/429.jpg"}]))
    for _ in range(EIM.MAX_FETCH_FAILURES - 1):
        counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
        assert counts["fetch_failed"] == 1
    (img,) = _read(f)["images"]
    assert img["vision_attempts"] == 0 and img["vision_check"] == "pending"
    assert not img.get("needs_review")
    counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert counts["needs_human"] == 1  # a dead link is surfaced, eventually
    assert _read(f)["images"][0]["needs_review"] is True


def test_a_truncated_download_is_not_judged(monkeypatch):
    class _R:
        status_code = 200
        content = b"x" * EIM._SAFE_FETCH_MAX_BYTES

    monkeypatch.setattr("src.utils.url_hygiene.safe_get", lambda *a, **k: _R())
    assert EIM._fetch_bytes("https://x/huge.jpg") is None


def test_duplicates_of_an_earlier_run_are_rejected(tmp_path, vision, monkeypatch):
    """The verified image's hash is stored, so the duplicate check spans runs."""
    _, calls = vision
    f = _write(tmp_path, _specific([{"url": "https://x/a.jpg"}]))
    EIM.resolve_equipment_images(tmp_path, grok_client=object(), images_per_record=3)
    rec = _read(f)
    assert (
        rec["images"][0]["phash"] == "https://x/a.jpg"
    )  # (the fixture hash is the URL)
    rec["images"].append({"url": "https://x/a.jpg#copy", "vision_check": "pending"})
    f.write_text(json.dumps(rec), encoding="utf-8")
    import src.extraction.equipment as EQ

    # the copy decodes to the same picture (same perceptual hash)
    monkeypatch.setattr(EQ, "_compute_image_hash", lambda body: "https://x/a.jpg")
    EIM.resolve_equipment_images(tmp_path, grok_client=object(), images_per_record=3)
    assert _read(f)["images"][1]["vision_check"] == "rejected"
    assert calls == ["https://x/a.jpg"]  # the copy never reached vision


def test_candidates_carry_their_source_page_and_time(tmp_path, vision, monkeypatch):
    monkeypatch.setattr(
        EIM, "article_image_candidates", lambda title: [{"url": "https://c/1.jpg"}]
    )
    url = "https://en.wikipedia.org/wiki/M1_Garand"
    f = _write(
        tmp_path,
        _specific(
            [], external_data={"wikipedia_url": url}, wikipedia_wwii_relevant=True
        ),
    )
    EIM.resolve_equipment_images(tmp_path, grok_client=object())
    (img,) = _read(f)["images"]
    assert img["source_page_url"] == url and img["candidate_added_at"]


def test_a_low_confidence_grok_identity_is_re_decided(monkeypatch):
    seen = []
    import src.extraction.equipment as EQ

    monkeypatch.setattr(
        EQ,
        "_identity_scope",
        lambda rec, grok: seen.append(rec["common_name"]) or "generic",
    )
    rec = {
        "common_name": "Zebra contraption",
        "canonical_name": "Something",
        "identity_source": "grok_disambiguation",
    }
    assert EIM.record_scope(rec, grok_client=object()) == "generic"
    assert seen == ["Zebra contraption"]
    rec["identity_source"] = "alias"
    assert EIM.record_scope(rec, grok_client=object()) == "specific"


def test_a_batch_queued_scope_leaves_the_record_untouched(
    tmp_path, vision, monkeypatch
):
    from src.grok_client import BatchModeCollecting
    import src.extraction.equipment as EQ

    def queued(rec, grok):
        raise BatchModeCollecting("k")

    monkeypatch.setattr(EQ, "_identity_scope", queued)
    raw = {"common_name": "Zebra contraption", "images": [{"url": "https://x/1.jpg"}]}
    f = _write(tmp_path, raw)
    before = f.read_text()
    counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert counts["scope_deferred"] == 1
    assert f.read_text() == before


def test_the_phase3_batch_rerun_runs_equipment_images(monkeypatch):
    import types

    import phase3_enrich_data as P
    import src.enrichment.bibliography_resolver as B

    for fn in (
        "enrich_groups_data",
        "enrich_all_places",
        "link_parent_place_ids",
        "enrich_bibliography",
    ):
        monkeypatch.setattr(P, fn, lambda *a, **k: 0)
    monkeypatch.setattr(B, "resolve_bibliography_dir", lambda *a, **k: {"resolved": 0})
    ran = []
    monkeypatch.setattr(
        P, "_resolve_equipment_images", lambda out, grok, cfg: ran.append(out) or 2
    )
    args = types.SimpleNamespace(
        people_only=False, max_items=0, output_dir=__import__("pathlib").Path("/tmp/x")
    )
    assert P._run_batch_rerun(args, {"supplemental_material": {}}, object()) == 2
    assert len(ran) == 1


def test_an_image_waiting_for_a_human_is_not_refetched_or_realerted(
    tmp_path, vision, monkeypatch
):
    fetched = []
    monkeypatch.setattr(EIM, "_fetch_bytes", lambda url: fetched.append(url))
    _write(
        tmp_path,
        _specific(
            [
                {
                    "url": "https://x/dead.jpg",
                    "vision_check": "pending",
                    "needs_review": True,
                }
            ]
        ),
    )
    counts = EIM.resolve_equipment_images(tmp_path, grok_client=object())
    assert fetched == [] and counts["needs_human"] == 0
