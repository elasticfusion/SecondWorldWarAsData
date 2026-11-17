"""Source-section enrichment must update the REAL record in place (XChannel-8 E2E, 2026-11-07).

The event pass emits ``output/content/<book>/source_section/<id>.json``. The enrichment pass
(Phase 2 step 4b) only looked in ``output/source_section/``, never found it, minted a NEW
SourceSectionID, wrote the summary/operation/articles and 7 Wikipedia images against that ghost ID,
and saved the ghost to a directory that is never synced: the persisted record stayed EMPTY and the
images' SourceSectionID dangled (validate_phase3 FAIL images.SourceSectionID -> source_section=7).
"""

import json
from pathlib import Path
from unittest.mock import patch

import src.extraction.source_section as ss

EVENT_ID = "01M6SYKS8T0TTYKFFNRYAA1D8B"
SECTION_ID = "01M6SYKS8T0TTYKFFNRYAA1D8C"


def _world(tmp_path: Path) -> tuple:
    """output_root/content/B/{chapter-event.json, source_section/<id>.json (EMPTY)}."""
    out = tmp_path / "output"
    book = out / "content" / "B"
    (book / "source_section").mkdir(parents=True)
    (book / "chapter-event.json").write_text(
        json.dumps({"Event": {"EventID": EVENT_ID, "Event_Name": "Normandy"}})
    )
    rec = {
        "SourceSectionID": SECTION_ID,
        "EventID": EVENT_ID,
        "section_title": None,
        "section_summary": None,
        "operation": None,
        "reference_articles": None,
        "wikipedia_checked_at": None,
        "source": {"book": "B"},
    }
    path = book / "source_section" / f"{SECTION_ID}.json"
    path.write_text(json.dumps(rec))
    return out, book, path


def _enrich(out: Path, book: Path):
    def _derive(record, *_a, **_k):
        record["section_summary"] = "Allied landings."
        record["operation"] = {"name": "Normandy landings"}
        return True

    def _media(record, output_dir, *_a, **_k):
        # what fetch_section_media does: images are keyed by the record's SourceSectionID
        (output_dir / "images").mkdir(exist_ok=True)
        (output_dir / "images" / "img.json").write_text(
            json.dumps({"SourceSectionID": record["SourceSectionID"]})
        )
        return 1

    with (
        patch.object(ss, "derive_summary_and_operation", _derive),
        patch.object(ss, "fetch_section_articles", lambda r: False),
        patch("src.extraction.source_section_media.fetch_section_media", _media),
    ):
        return ss._enrich_one_section(book / "chapter-event.json", out, object(), 0.5)


def test_existing_record_under_content_is_found_and_enriched_in_place(tmp_path):
    out, book, path = _world(tmp_path)
    assert ss._find_section_record_by_event_id(out, EVENT_ID) == path
    assert _enrich(out, book) == "enriched"
    saved = json.loads(path.read_text())
    assert saved["SourceSectionID"] == SECTION_ID  # same ID: no ghost minted
    assert saved["section_summary"] == "Allied landings."
    assert saved["operation"] == {"name": "Normandy landings"}


def test_images_reference_the_persisted_section_id_not_a_ghost(tmp_path):
    out, book, path = _world(tmp_path)
    _enrich(out, book)
    img = json.loads((out / "images" / "img.json").read_text())
    assert img["SourceSectionID"] == SECTION_ID
    assert img["SourceSectionID"] == json.loads(path.read_text())["SourceSectionID"]


def test_no_unsynced_ghost_directory_is_created(tmp_path):
    out, book, path = _world(tmp_path)
    _enrich(out, book)
    assert not (out / "source_section").exists()  # the dir the sync never uploads
    assert [p.name for p in (book / "source_section").glob("*.json")] == [path.name]


def test_missing_record_is_built_next_to_its_event_not_in_a_ghost_dir(tmp_path):
    out, book, path = _world(tmp_path)
    path.unlink()
    assert _enrich(out, book) == "enriched"
    created = list((book / "source_section").glob("*.json"))
    assert len(created) == 1 and created[0].name != path.name
    assert not (out / "source_section").exists()


def test_legacy_top_level_dir_is_still_searched(tmp_path):
    out = tmp_path / "output"
    (out / "source_section").mkdir(parents=True)
    p = out / "source_section" / "x.json"
    p.write_text(json.dumps({"EventID": EVENT_ID}))
    assert ss._find_section_record_by_event_id(out, EVENT_ID) == p


def test_gate_stamps_persist_even_when_nothing_was_found(tmp_path):
    """A section with no Wikipedia hit / no images must still persist media_checked_at, or every
    re-run repeats the network calls."""
    out, book, path = _world(tmp_path)

    def _nothing(record, *_a, **_k):
        return False

    def _media_none(record, output_dir, *_a, **_k):
        record["media_checked_at"] = (
            "2026-11-07"  # fetch_section_media stamps it up front
        )
        return 0

    with (
        patch.object(ss, "derive_summary_and_operation", _nothing),
        patch.object(ss, "fetch_section_articles", lambda r: False),
        patch("src.extraction.source_section_media.fetch_section_media", _media_none),
    ):
        status = ss._enrich_one_section(book / "chapter-event.json", out, object(), 0.5)
    assert status == "noop"
    assert json.loads(path.read_text())["media_checked_at"] == "2026-11-07"


def test_refused_save_is_an_error_not_a_silent_enriched(tmp_path, monkeypatch):
    out, book, path = _world(tmp_path)
    alerts = []
    import src.utils.observe as obs

    monkeypatch.setattr(obs, "observe_warn", lambda *a, **k: alerts.append(a[1]) or "")
    monkeypatch.setattr(ss, "write_json_with_lock", lambda *a, **k: False)
    assert _enrich(out, book) == "error"
    assert alerts == ["save_refused"]


def test_rebuilding_a_missing_record_is_visible(tmp_path):
    out, book, path = _world(tmp_path)
    path.unlink()
    import src.utils.observe as obs

    obs.reset_counted_warnings()
    _enrich(out, book)
    assert obs.counted_warning_totals("source_section", "record_rebuilt")


def test_shared_layout_helper_covers_real_and_legacy_locations(tmp_path):
    from src.utils.source_sections import iter_section_files, section_dirs

    real = tmp_path / "content" / "B" / "source_section"
    legacy = tmp_path / "source_section"
    for d in (real, legacy):
        d.mkdir(parents=True)
        (d / "a.json").write_text("{}")
        (d / "index.json").write_text("{}")  # skipped
    assert list(iter_section_files(tmp_path)) == [real / "a.json", legacy / "a.json"]
    assert section_dirs(tmp_path) == [real, legacy]  # real layout first


def test_other_readers_find_records_in_the_real_layout(tmp_path):
    """event_provenance / image_place_link / intra_corpus read the same layout (the last two
    looked only in output/source_section and silently saw nothing in production)."""
    from src.extraction import event_provenance as ep
    from src.extraction import image_place_link as ipl
    from src.extraction import intra_corpus as ic

    d = tmp_path / "content" / "Bk" / "source_section"
    d.mkdir(parents=True)
    rec = {
        "SourceSectionID": "01M6SYKS8T0TTYKFFNRYAA1D8C",
        "EventID": EVENT_ID,
        "source": {
            "book": "Bk",
            "source_url": "https://www.ibiblio.org/hyperwar/USA/USA-E-X/USA-E-X-1.html",
        },
    }
    (d / "r.json").write_text(json.dumps(rec))
    assert ep._build_event_section_index(tmp_path) == {EVENT_ID: rec["SourceSectionID"]}
    assert ipl._build_book_section_index(tmp_path).get("bk") == rec["SourceSectionID"]
    assert ic._corpus_volumes_present(tmp_path)


def test_openserp_source_sections_iterates_the_real_layout(tmp_path, monkeypatch):
    import src.enrichment.openserp_enrichment as oe

    d = tmp_path / "content" / "Bk" / "source_section"
    d.mkdir(parents=True)
    (d / "r.json").write_text(
        json.dumps({"SourceSectionID": "01M6SYKS8T0TTYKFFNRYAA1D8C"})
    )
    seen = []
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda u: True)
    monkeypatch.setattr(
        oe,
        "_process_one_source_section",
        lambda f, data, *a: seen.append(f.name) or False,
    )
    oe.enrich_source_sections_with_openserp(tmp_path, "http://x")
    assert seen == ["r.json"]
