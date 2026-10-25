"""Regression for Phase-3 review H3/M2: the batch re-run called enrichers directly (a raise aborted
the phase, skipping teardown/validation) and reset total_enriched (misreported). _run_batch_rerun is
now per-source fail-safe and returns an accumulable count."""

import types
import src.enrichment  # noqa
import phase3_enrich_data as P


def _args(**kw):
    d = {
        "people_only": False,
        "max_items": 0,
        "output_dir": __import__("pathlib").Path("/tmp/x"),
    }
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_batch_rerun_people_only_is_noop():
    assert P._run_batch_rerun(_args(people_only=True), {}, object()) == 0


def test_batch_rerun_failsafe_on_raising_enricher(monkeypatch):
    # A raising enricher must NOT propagate (would abort phase teardown) — step returns 0, others run.
    monkeypatch.setattr(
        P,
        "enrich_groups_data",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(P, "enrich_all_places", lambda *a, **k: 3)
    monkeypatch.setattr(P, "link_parent_place_ids", lambda *a, **k: None)
    monkeypatch.setattr(P, "enrich_bibliography", lambda *a, **k: 2)
    import src.enrichment.bibliography_resolver as B

    monkeypatch.setattr(B, "resolve_bibliography_dir", lambda *a, **k: {"resolved": 4})
    total = P._run_batch_rerun(_args(), {"supplemental_material": {}}, object())
    assert (
        total == 3 + 2 + 4
    )  # groups raised -> 0, others counted; no exception propagated


def test_batch_rerun_accumulates_all_sources(monkeypatch):
    monkeypatch.setattr(P, "enrich_groups_data", lambda *a, **k: 1)
    monkeypatch.setattr(P, "enrich_all_places", lambda *a, **k: 1)
    monkeypatch.setattr(P, "link_parent_place_ids", lambda *a, **k: None)
    monkeypatch.setattr(P, "enrich_bibliography", lambda *a, **k: 1)
    import src.enrichment.bibliography_resolver as B

    monkeypatch.setattr(B, "resolve_bibliography_dir", lambda *a, **k: {"resolved": 1})
    assert P._run_batch_rerun(_args(), {"supplemental_material": {}}, object()) == 4


def test_m1_external_maps_resolves_to_maps_entity(tmp_path):
    """M1: output/external_maps/ records must resolve to the 'maps' entity so the schema guard +
    validation_stats apply (they were silently bypassed as an unregistered 'external_maps' dir).
    """
    from src.utils.file_lock import _resolve_entity_type, write_json_with_lock
    import src.utils.validation_stats as V

    assert _resolve_entity_type(tmp_path / "external_maps" / "m.json") == "maps"
    V.reset_stats()
    d = tmp_path / "external_maps"
    d.mkdir(parents=True)
    write_json_with_lock(
        d / "m.json",
        {"MapID": "01HX7YZABCDEFGHJKMNPQRSTVW", "map_title": "T"},
        entity="maps",
    )
    assert "maps" in V.get_stats()["by_entity"]  # now counted (was bypassed)
    V.reset_stats()
