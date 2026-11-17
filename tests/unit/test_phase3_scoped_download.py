"""A book-scoped Phase 3 must download every entity directory it enriches: per-book types from the
book manifest, shared types in full. A dir in neither got only its index.json, so a single-book run
had no image, map or narrative-note records (PHASE3_MAP gap 27)."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
import ecs_entrypoint as EE
from src.utils.book_manifest import MANIFESTED_ENTITY_TYPES, BookManifest


def test_every_phase3_dir_is_manifested_or_downloaded_in_full():
    dirs = set(EE._PHASE23_ENTITY_DIRS)
    manifested, shared = set(MANIFESTED_ENTITY_TYPES), set(EE._PHASE3_CROSS_BOOK_DIRS)
    assert dirs <= manifested | shared, dirs - manifested - shared
    assert not (
        manifested & shared - {"bibliography"}
    )  # bibliography: registered AND shared


def test_images_registered_per_book_are_read_back(tmp_path):
    m = BookManifest(local_dir=tmp_path)
    m.register("B", "images", "img_1.json")
    assert m.get_all_files("B")["images"] == ["img_1.json"]


def test_scoped_download_takes_images_from_the_manifest_and_shared_dirs_in_full(
    monkeypatch, tmp_path, caplog
):
    import logging

    monkeypatch.setenv("BOOK_NAME", "B")
    monkeypatch.setattr(EE, "WORKDIR", tmp_path)
    monkeypatch.setattr(EE, "_wait_for_reconcile_barrier", lambda: "seen")
    monkeypatch.setattr(EE, "_s3_client", lambda: object())
    monkeypatch.setattr(
        EE,
        "_get_book_entity_files",
        lambda s3, b: ["output/images/img_1.json", "output/people/p.json"],
    )
    got, full = [], []
    monkeypatch.setattr(
        EE, "_download_s3_file", lambda s3, key, optional=False: got.append(key)
    )
    monkeypatch.setattr(
        EE,
        "_download_s3_prefix_skip_existing",
        lambda s3, p, exclude=(): full.append((p, exclude)) or (2, 0),
    )
    monkeypatch.setattr(EE, "_download_phase3_provenance_inputs", lambda *a: None)
    monkeypatch.setattr(EE, "_observe_bibliography_download_coverage", lambda *a: None)
    with caplog.at_level(logging.INFO):
        EE._download_phase3_from_s3()
    assert "output/images/img_1.json" in got and "output/people/p.json" in got
    assert sorted(p for p, _ in full) == [
        "output/bibliography/",
        "output/maps/",
        "output/narrative_notes/",
        "output/supplemental/",
    ]
    assert all(ex == EE._PHASE3_DOWNLOAD_EXCLUDE for _, ex in full)
    line = next(
        r.message for r in caplog.records if "phase3.scoped_download" in r.message
    )
    assert "images=1" in line and "maps=2" in line and "narrative_notes=2" in line


class _ListingS3:
    def __init__(self, keys):
        self.keys = keys

    def get_paginator(self, _name):
        keys = self.keys

        class _P:
            def paginate(self, **_k):
                return [{"Contents": [{"Key": k} for k in keys]}]

        return _P()


def test_map_image_binaries_are_never_downloaded_for_phase3(monkeypatch, tmp_path):
    """Phase 3 never reads an existing map binary; downloading them made every task GET and then
    re-stage the whole corpus's map images for AV scan."""
    monkeypatch.setattr(EE, "WORKDIR", tmp_path)
    got = []
    monkeypatch.setattr(EE, "_download_s3_file", lambda s3, key, **k: got.append(key))
    s3 = _ListingS3(
        ["output/maps/m1.json", "output/maps/files/m1.jpg", "output/maps/index.json"]
    )
    downloaded, skipped = EE._download_s3_prefix_skip_existing(
        s3, "output/maps/", EE._PHASE3_DOWNLOAD_EXCLUDE
    )
    assert got == ["output/maps/m1.json", "output/maps/index.json"]
    assert (downloaded, skipped) == (2, 1)


def test_phase3_final_sync_uploads_only_what_the_task_changed(monkeypatch, tmp_path):
    """Unchanged downloaded records (e.g. other books' shared maps) are not re-uploaded; a record
    the task changed is."""
    import time

    monkeypatch.setattr(EE, "WORKDIR", tmp_path)
    people = tmp_path / "output" / "people"
    people.mkdir(parents=True)
    for name in ("same.json", "changed.json"):
        (people / name).write_text("{}")
        EE._downloaded_keys[f"output/people/{name}"] = (people / name).stat().st_mtime
    time.sleep(0.01)
    (people / "changed.json").write_text('{"x": 1}')
    seen = {}

    def fake_sync_up(d, prefix, skip_keys=None, **_k):
        seen[prefix] = skip_keys
        return 0, []

    monkeypatch.setattr(EE, "s3_sync_up", fake_sync_up)
    monkeypatch.setattr(EE, "_sync_maps_with_av_staging", lambda d, skip: (0, []))
    try:
        EE._final_sync("phase3_enrich_data.py")
    except (
        Exception
    ):  # noqa: BLE001 - later steps of the final sync need AWS; only the skip matters
        pass
    skip = seen["output/people"]
    assert (
        "output/people/same.json" in skip and "output/people/changed.json" not in skip
    )
    for name in ("same.json", "changed.json"):
        EE._downloaded_keys.pop(f"output/people/{name}", None)


def test_source_section_media_images_are_registered_for_the_book(monkeypatch, tmp_path):
    """Wikipedia media images were written with no manifest row, so a book-scoped Phase 3 never
    downloaded them."""
    import src.extraction.batch_parallel as BP
    import src.extraction.source_section_media as SSM

    registered = []
    monkeypatch.setattr(BP, "_register_entity", lambda *a: registered.append(a))
    monkeypatch.setattr(
        SSM, "_list_article_image_titles", lambda t, to: ["File:Omaha.jpg"]
    )
    monkeypatch.setattr(SSM, "_classify", lambda f: "photo")
    monkeypatch.setattr(
        SSM,
        "_resolve_commons_image",
        lambda f, to: {"url": "https://upload.wikimedia.org/x.jpg", "license": "PD"},
    )
    record = {
        "SourceSectionID": "01M7GFE9WDTBRMW7CJADT6K67F",
        "EventID": "1KSNBHH2FQQM84FB1ZNBDC2811",
        "operation": {"name": "Operation Overlord"},
        "source": {"book": "B"},
    }
    assert SSM.fetch_section_media(record, tmp_path, download=False) == 1
    assert [(b, t) for b, t, _f in registered] == [("B", "images")]


def test_only_maps_still_owing_vision_need_their_image():
    from src.extraction.map_common import needs_vision_image

    ibiblio = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/maps/m8.jpg"
    other = "https://example.org/a/b1.jpg"
    assert needs_vision_image({"map_title": "Map VIII", "source_url": other})
    assert not needs_vision_image(
        {"source_url": other, "map_vision_checked_at": "2026-11-16Z"}
    )
    assert not needs_vision_image(
        {"map_title": "Map VIII", "source_url": ibiblio}
    )  # tier 1


def test_phase3_fetches_the_binary_of_a_map_still_owing_vision(monkeypatch, tmp_path):
    """Excluding the binaries from the bulk download must not stop the vision retry: the map whose
    check is still owed gets its image; decided and deterministic maps do not."""
    import json

    maps = tmp_path / "output" / "maps"
    maps.mkdir(parents=True)
    other = "https://example.org/a/b1.jpg"
    records = {
        "pending.json": {
            "source_url": other,
            "local_image_path": "output/maps/files/P.jpg",
        },
        "decided.json": {
            "source_url": other,
            "local_image_path": "output/maps/files/D.jpg",
            "map_vision_checked_at": "2026-11-16Z",
        },
        "no_image.json": {"source_url": other},
        "index.json": {"https://x": "pending.json"},
    }
    for name, rec in records.items():
        (maps / name).write_text(json.dumps(rec))
    monkeypatch.setattr(EE, "WORKDIR", tmp_path)
    got = []

    def fake_get(s3, key, optional=False):
        got.append(key)
        (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / key).write_bytes(b"img")

    monkeypatch.setattr(EE, "_download_s3_file", fake_get)
    assert EE._download_pending_map_images(object()) == 1
    assert got == ["output/maps/files/P.jpg"]
