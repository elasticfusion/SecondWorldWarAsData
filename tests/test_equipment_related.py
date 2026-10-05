"""related_equipment: narrative-sourced record-level relationships, auto-create of
distinct related records, and merge accumulation. Inline sub-designations are NOT here
(that's enforced by the prompt discriminator, not this linker)."""

import json
from pathlib import Path

from src.extraction.equipment import (
    EquipmentExtraction,
    _link_related_equipment,
    _merge_related_equipment,
)
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA


def _rel(**kw):
    base = {
        "common_name": "M4 Sherman",
        "technical_identifier": "M4",
        "category": "armor",
    }
    base["related_equipment"] = [kw]
    return EquipmentExtraction.model_validate(base).related_equipment


def test_schema_declares_related_equipment():
    props = EQUIPMENT_OUTPUT_SCHEMA["properties"]
    assert "related_equipment" in props
    item = props["related_equipment"]["items"]["properties"]
    for f in ("relationship", "name", "basis", "EquipmentID", "original_text"):
        assert f in item


def test_relationship_recorded_with_basis_and_original_text(tmp_path):
    rels = _rel(
        relationship="variant",
        name="M4(76)W",
        basis="76mm gun vs the standard 75mm",
        original_text="an M4 up-gunned with a 76mm in place of the 75mm",
    )
    out = _link_related_equipment(rels, {}, tmp_path)
    assert out[0]["relationship"] == "variant"
    assert out[0]["basis"] == "76mm gun vs the standard 75mm"
    assert out[0]["original_text"]


def test_autocreates_minimal_distinct_record(tmp_path):
    rels = _rel(
        relationship="successor",
        name="M26 Pershing",
        original_text="the M26 Pershing would succeed the Sherman",
    )
    idx = {}
    out = _link_related_equipment(rels, idx, tmp_path)
    # a real EquipmentID was assigned + a minimal record file created + indexed
    assert out[0]["EquipmentID"]
    assert "M26 Pershing" in idx
    created = json.loads(Path(idx["M26 Pershing"]).read_text())
    assert created["common_name"] == "M26 Pershing"
    assert created["EquipmentID"] == out[0]["EquipmentID"]
    assert created["event_mentions"] == []  # minimal: no mention of its own yet


def test_resolves_existing_record_without_duplicating(tmp_path):
    # Pre-existing M26 record in the index -> link resolves to it, no new file.
    existing = tmp_path / "M26.json"
    existing.write_text(
        json.dumps(
            {"EquipmentID": "01EXISTINGM26000000000000", "common_name": "M26 Pershing"}
        )
    )
    idx = {"M26 Pershing": existing}
    before = list(tmp_path.iterdir())
    out = _link_related_equipment(
        _rel(relationship="successor", name="M26 Pershing"), idx, tmp_path
    )
    assert out[0]["EquipmentID"] == "01EXISTINGM26000000000000"
    assert list(tmp_path.iterdir()) == before  # no new file created


def test_merge_accumulates_and_dedups():
    existing = {
        "related_equipment": [{"relationship": "successor", "name": "M26 Pershing"}]
    }
    incoming = {
        "related_equipment": [
            {"relationship": "successor", "name": "M26 Pershing"},  # dup -> collapse
            {"relationship": "predecessor", "name": "M3 Lee"},
        ]
    }  # new -> add
    _merge_related_equipment(existing, incoming)
    names = {(r["relationship"], r["name"]) for r in existing["related_equipment"]}
    assert names == {("successor", "M26 Pershing"), ("predecessor", "M3 Lee")}
