"""Tests for phase3_retry.py."""

# pylint: disable=missing-function-docstring

import json

from phase3_retry import count_unenriched_people


class TestCountUnenrichedPeople:
    def test_counts_missing_enrichment(self, tmp_path):
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        # Unenriched
        (people_dir / "a.json").write_text(json.dumps({"name": "A"}), encoding="utf-8")
        # Enriched
        (people_dir / "b.json").write_text(
            json.dumps({"name": "B", "enrichment_data": {"bio": "x"}}),
            encoding="utf-8",
        )
        # Not found (should not count)
        (people_dir / "c.json").write_text(
            json.dumps({"name": "C", "enrichment_status": "not_found"}),
            encoding="utf-8",
        )

        assert count_unenriched_people(people_dir) == 1

    def test_skips_index_files(self, tmp_path):
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        (people_dir / "index.json").write_text("{}", encoding="utf-8")
        (people_dir / "duplicate_report.json").write_text("{}", encoding="utf-8")

        assert count_unenriched_people(people_dir) == 0

    def test_returns_zero_if_dir_missing(self, tmp_path):
        assert count_unenriched_people(tmp_path / "nope") == 0

    def test_handles_corrupted_files(self, tmp_path):
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        (people_dir / "bad.json").write_text("not json", encoding="utf-8")
        (people_dir / "good.json").write_text(
            json.dumps({"name": "Good"}), encoding="utf-8"
        )

        assert count_unenriched_people(people_dir) == 1
