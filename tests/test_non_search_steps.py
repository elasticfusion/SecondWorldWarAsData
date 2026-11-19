"""Task 1.4: the steps that work on the book's own documents run whether or not OpenSERP (web
search) is on or reachable, and `supplemental_material.use_openserp: false` is honoured on ECS:
the service is not started and the flag is never forced back on."""

import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402

NON_SEARCH = [
    "image_person_link",
    "link_asset_review",
    "image_place_link",
    "intra_corpus_links",
    "event_provenance_stamp",
]


def _phase3():
    import importlib

    return importlib.import_module("phase3_enrich_data")


def test_the_document_link_steps_run_without_openserp(tmp_path, monkeypatch):
    p3 = _phase3()
    for fn in (
        "_link_images_to_people",
        "_review_external_links",
        "_link_images_to_places",
        "_process_intra_corpus_links",
        "_stamp_event_provenance",
    ):
        monkeypatch.setattr(p3, fn, lambda *a, **k: 1)
    ran = []

    def step(name, fn):
        ran.append(name)
        return fn()

    args = SimpleNamespace(output_dir=tmp_path)
    assert p3._run_document_link_steps(args, {}, None, step) == len(NON_SEARCH)
    assert ran == NON_SEARCH


def test_each_document_link_step_keeps_its_own_switch(tmp_path, monkeypatch):
    p3 = _phase3()
    for fn in (
        "_link_images_to_people",
        "_review_external_links",
        "_link_images_to_places",
        "_process_intra_corpus_links",
        "_stamp_event_provenance",
    ):
        monkeypatch.setattr(p3, fn, lambda *a, **k: 0)
    ran = []
    off = {
        "images": {"link_people": False, "link_places": False},
        "links": {"review_external": False, "intra_corpus": False},
    }
    p3._run_document_link_steps(
        SimpleNamespace(output_dir=tmp_path), off, None, lambda n, f: ran.append(n) or 0
    )
    assert ran == ["event_provenance_stamp"]  # the provenance stamp always runs


def test_the_openserp_group_holds_only_web_search(tmp_path, monkeypatch):
    p3 = _phase3()
    import src.enrichment.openserp_enrichment as oe

    for fn in (
        "enrich_people_with_openserp",
        "enrich_equipment_with_openserp",
        "enrich_groups_with_openserp",
        "enrich_places_with_openserp",
        "enrich_source_sections_with_openserp",
        "reset_metrics",
        "write_metrics",
    ):
        monkeypatch.setattr(oe, fn, lambda *a, **k: 0)
    monkeypatch.setattr(p3, "_update_lock_status", lambda *a: None)
    ran = []
    args = SimpleNamespace(output_dir=tmp_path, max_items=0)
    p3._run_openserp_enrichment(args, {}, None, lambda n, f: ran.append(n) or 0)
    assert not set(ran) & set(NON_SEARCH)


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "config", [{"supplemental_material": {"use_openserp": False}}, {}]
)  # absent = off, the same default as phase3_enrich_data
def test_with_openserp_off_the_service_is_not_started(monkeypatch, config):
    monkeypatch.setattr(ee, "_config_or_empty", lambda: config)
    monkeypatch.setattr(ee.boto3, "client", mock.Mock(side_effect=AssertionError))
    ee._start_openserp_if_needed("phase3_enrich_data.py")  # no ECS call, no wait


def test_the_service_address_patch_never_turns_openserp_on(tmp_path, monkeypatch):
    import yaml

    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"supplemental_material": {"use_openserp": False}}))
    real_path = ee.Path
    monkeypatch.setattr(
        ee, "Path", lambda p: cfg if p == "/app/config.yaml" else real_path(p)
    )
    ee._patch_openserp_url("10.0.0.5")
    out = yaml.safe_load(cfg.read_text())
    assert out["supplemental_material"]["use_openserp"] is False
    assert out["external_maps"]["openserp_url"] == "http://10.0.0.5:7001"
