"""Tests for the Phase-1 parse guard (HIGH-4b): content-validity + empty-parse flag only."""

import json

import src.utils.file_lock as fl
import src.utils.validation_stats as vs


def setup_function(_):
    vs.reset_stats()


def _valid_parsed():
    return {
        "book": "The Ardennes",
        "chapter_title": "COBRA",
        "paragraphs": [{"absolute_number": 1, "text": "The attack opened at 1100."}],
    }


def test_valid_parsed_writes_and_records_allow(tmp_path):
    p = tmp_path / "chapter1-parsed.json"
    assert fl.write_parsed_with_guard(p, _valid_parsed()) is True
    assert p.exists()
    assert json.loads(p.read_text())["chapter_title"] == "COBRA"
    assert vs.get_stats()["totals"].get("allow") == 1


def test_missing_backbone_field_blocks(tmp_path):
    p = tmp_path / "bad-parsed.json"
    rec = {"book": "X", "paragraphs": []}  # missing chapter_title
    assert fl.write_parsed_with_guard(p, rec) is False
    assert not p.exists()  # garbage not persisted
    assert vs.get_stats()["totals"].get("block") == 1


def test_paragraph_missing_text_blocks(tmp_path):
    p = tmp_path / "bad2-parsed.json"
    rec = {
        "book": "X",
        "chapter_title": "C",
        "paragraphs": [{"absolute_number": 1}],  # no text
    }
    assert fl.write_parsed_with_guard(p, rec) is False
    assert not p.exists()


def test_empty_parse_is_flagged_but_written(tmp_path):
    # Zero usable paragraphs -> allow_empty, but still written (an empty plate page is legitimate).
    p = tmp_path / "empty-parsed.json"
    rec = {"book": "X", "chapter_title": "Plate 1", "paragraphs": []}
    assert fl.write_parsed_with_guard(p, rec) is True
    assert p.exists()
    totals = vs.get_stats()["totals"]
    assert totals.get("allow_empty") == 1
    assert "allow" not in totals  # empty path records allow_empty, not allow


def test_whitespace_only_paragraphs_count_as_empty(tmp_path):
    p = tmp_path / "ws-parsed.json"
    rec = {
        "book": "X",
        "chapter_title": "C",
        "paragraphs": [{"absolute_number": 1, "text": "   "}],
    }
    assert fl.write_parsed_with_guard(p, rec) is True
    assert vs.get_stats()["totals"].get("allow_empty") == 1


def test_parsed_block_clusters_surface_systematically(tmp_path):
    # A book that systematically parses to garbage shows up as a 'parsed' block cluster.
    for i in range(12):
        fl.write_parsed_with_guard(tmp_path / f"b{i}-parsed.json", {"book": "X"})
    clusters = {(c["entity"], c["outcome"]) for c in vs.systematic_clusters()}
    assert ("parsed", "block") in clusters


def test_guard_parsed_content_shared_helper(tmp_path):
    """The shared helper (used by the S3/Lambda path, no local file) returns False on invalid and
    records the same catcher outcomes."""
    # invalid -> False + block
    assert fl.guard_parsed_content("x-parsed.json", {"book": "X"}) is False
    assert vs.get_stats()["totals"].get("block") == 1
    # valid -> True + allow
    assert fl.guard_parsed_content("y-parsed.json", _valid_parsed()) is True
    assert vs.get_stats()["totals"].get("allow") == 1
    # empty -> True + allow_empty
    assert (
        fl.guard_parsed_content(
            "z-parsed.json", {"book": "X", "chapter_title": "Plate", "paragraphs": []}
        )
        is True
    )
    assert vs.get_stats()["totals"].get("allow_empty") == 1


def test_fail_safe_writes_on_validator_error(tmp_path, monkeypatch):
    # If validation itself errors, the parse is still written (never lost to a telemetry bug).
    import jsonschema

    def _boom(*a, **k):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(jsonschema, "validate", _boom)
    p = tmp_path / "fs-parsed.json"
    assert fl.write_parsed_with_guard(p, _valid_parsed()) is True
    assert p.exists()
