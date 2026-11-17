"""Casualty dedup reads only casualty records - never the run's state files."""

import json
import logging

from src.extraction import casualties as C


def test_state_files_are_not_read_as_records(tmp_path, caplog):
    (tmp_path / ".processed_events.json").write_text(json.dumps(["E1", "E2"]))
    (tmp_path / "index.json").write_text("[]")
    with caplog.at_level(logging.WARNING):
        assert C._existing_statements(tmp_path) == set()
    assert "unreadable_record" not in caplog.text


def test_a_corrupt_record_is_still_reported(tmp_path, caplog):
    (tmp_path / "cas_1.json").write_text("[1, 2]")
    with caplog.at_level(logging.WARNING):
        C._existing_statements(tmp_path)
    assert "unreadable_record file=cas_1.json" in caplog.text
