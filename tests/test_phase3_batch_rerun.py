"""Phase-3 regressions (M1: external_maps resolves to the maps entity). The batch re-run tests moved
to tests/test_phase3_rounds.py with the rounds loop that replaced ``_run_batch_rerun``.
"""


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
