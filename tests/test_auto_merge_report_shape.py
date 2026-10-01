"""Regression tests for _auto_merge_entity_type report-shape handling.

The real duplicate_report.json stores the group LIST under "duplicates" and an
INT count under "duplicate_groups". A prior bug read "duplicate_groups" (the int)
and iterated it -> "'int' object is not iterable", which failed dedup detection
for EVERY document and blocked the whole pipeline at the dedup gate (never
reaching Phase 3). These tests pin the correct behavior.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee


def _write_report(entity_dir: Path, report: dict) -> None:
    entity_dir.mkdir(parents=True, exist_ok=True)
    (entity_dir / "duplicate_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )


def test_auto_merge_reads_duplicates_list_not_int_count(tmp_path):
    """The real report shape: duplicate_groups is an INT, the list is 'duplicates'.
    Must not raise 'int' object is not iterable."""
    entity_dir = tmp_path / "people"
    _write_report(
        entity_dir,
        {
            "total_people": 2,
            "duplicate_groups": 1,  # INT count — must NOT be iterated
            "duplicates": [
                {"people": [{"name": "John Smith"}, {"name": "John Smith"}]}
            ],
        },
    )
    with (
        patch.object(ee, "merge_generic", create=True),
        patch("src.dedup.merge.merge_generic"),
    ):
        merged = ee._auto_merge_entity_type(entity_dir, "PersonID")
    # One exact-duplicate pair -> one merge (len-1). Crucially: no exception.
    assert merged == 1


def test_auto_merge_int_count_only_does_not_raise(tmp_path):
    """A report with ONLY the int count (no list) must return 0, not raise."""
    entity_dir = tmp_path / "places"
    _write_report(entity_dir, {"duplicate_groups": 5})
    assert ee._auto_merge_entity_type(entity_dir, "PlaceID") == 0


def test_auto_merge_legacy_groups_key_still_works(tmp_path):
    """Backwards-compat: a report using the 'groups' list key still merges."""
    entity_dir = tmp_path / "equipment"
    _write_report(
        entity_dir,
        {"groups": [{"people": [{"name": "M4 Sherman"}, {"name": "M4 Sherman"}]}]},
    )
    with patch("src.dedup.merge.merge_generic"):
        merged = ee._auto_merge_entity_type(entity_dir, "EquipmentID")
    assert merged == 1


def test_auto_merge_no_report_returns_zero(tmp_path):
    """No report file -> 0, no error."""
    assert ee._auto_merge_entity_type(tmp_path / "nope", "PersonID") == 0
