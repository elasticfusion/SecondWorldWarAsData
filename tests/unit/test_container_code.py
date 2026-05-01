"""Unit tests for batch_api, ecs_entrypoint, phase1_parse, and dedup scripts."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.batch_api import (
    BatchCollector,
    BatchMetrics,
    BatchRequest,
    BatchResult,
    RequestDetail,
)


class TestBatchResult:
    def test_ok(self):
        r = BatchResult(request_id="r1", content="data", finish_reason="stop")
        assert r.ok
        assert not r.truncated
        assert r.status == "valid"

    def test_truncated(self):
        r = BatchResult(request_id="r1", content="data", finish_reason="length")
        assert not r.ok
        assert r.truncated
        assert r.status == "truncated"

    def test_error(self):
        r = BatchResult(request_id="r1", error="fail")
        assert not r.ok
        assert r.status == "error"

    def test_other_finish(self):
        r = BatchResult(request_id="r1", content="data", finish_reason="unknown")
        assert not r.ok
        assert r.status == "other_finish"


class TestBatchCollector:
    def test_add_and_dedup(self):
        c = BatchCollector()
        r1 = BatchRequest("id1", [], "model", 0.1, "events")
        r2 = BatchRequest("id1", [], "model", 0.1, "events")  # duplicate
        r3 = BatchRequest("id2", [], "model", 0.1, "dates")
        c.add(r1)
        c.add(r2)
        c.add(r3)
        assert len(c) == 2

    def test_write_jsonl(self, tmp_path):
        c = BatchCollector()
        c.add(
            BatchRequest(
                "id1", [{"role": "user", "content": "hi"}], "grok", 0.1, "events"
            )
        )
        path = tmp_path / "batch.jsonl"
        count = c.write_jsonl(path)
        assert count == 1
        line = json.loads(path.read_text().strip())
        assert line["custom_id"] == "id1"
        assert line["body"]["model"] == "grok"


class TestBatchMetrics:
    def test_add_detail(self):
        m = BatchMetrics(batch_id="b1", total_requests=10)
        d = RequestDetail(request_id="r1", status="valid", content_length=100)
        m.add_detail(d)
        assert len(m.request_details) == 1

    def test_to_dict(self):
        m = BatchMetrics(batch_id="b1", valid=5, truncated=1)
        d = m.to_dict()
        assert d["valid"] == 5
        assert d["truncated"] == 1
        assert "request_details" in d


class TestPhase1Parse:
    def test_doc_to_dict(self):
        from phase1_parse import _doc_to_dict

        doc = MagicMock()
        doc.book = "TestBook"
        doc.chapter_number = 1
        doc.chapter_title = "Chapter 1"
        doc.section_id = "a"
        doc.author = "Author"
        doc.series = "Series"
        doc.license = "PD"
        doc.file_path = Path("/test.md")
        doc.paragraphs = []
        doc.images = []
        doc.maps = []
        doc.footnotes = []

        result = _doc_to_dict(doc)
        assert result["book"] == "TestBook"
        assert result["chapter_number"] == 1
        assert result["paragraphs"] == []

    def test_is_footnotes_chapter(self):
        from phase1_parse import _is_footnotes_chapter

        doc = MagicMock()
        doc.chapter_title = "Endnotes"
        doc.paragraphs = []
        assert _is_footnotes_chapter(doc)

        doc.chapter_title = "Battle of Normandy"
        assert not _is_footnotes_chapter(doc)

    def test_is_footnotes_chapter_from_text(self):
        from phase1_parse import _is_footnotes_chapter

        doc = MagicMock()
        doc.chapter_title = "Notes"
        para = MagicMock()
        para.text = "These are footnote references"
        doc.paragraphs = [para]
        assert _is_footnotes_chapter(doc)


class TestFindDuplicateGroups:
    def test_numbers_match(self):
        from scripts.find_duplicate_groups import _numbers_match

        assert _numbers_match("1st Infantry", "1st Infantry Division")
        assert not _numbers_match("1st Infantry", "2nd Infantry")
        assert _numbers_match("First Army", "1st Army")
        assert not _numbers_match("VII Corps", "VIII Corps")
        assert _numbers_match("Panzer Lehr", "Panzer Lehr Division")
        assert not _numbers_match("I Corps", "1st Corps")  # roman != arabic
        assert _numbers_match("2d Infantry", "2nd Infantry")

    def test_extract_numbers(self):
        from scripts.find_duplicate_groups import _extract_numbers

        assert "1" in _extract_numbers("1st Infantry Division")
        assert "2" in _extract_numbers("2d Panzer")
        assert "2" in _extract_numbers("2nd Panzer")
        assert _extract_numbers("Panzer Lehr") == set()

    def test_find_duplicate_groups(self, tmp_path):
        from scripts.find_duplicate_groups import find_duplicate_groups

        # Create test group files
        (tmp_path / "1st infantry.json").write_text(
            json.dumps({"group_name": "1st Infantry", "GroupID": "01A"})
        )
        (tmp_path / "1st infantry division.json").write_text(
            json.dumps({"group_name": "1st Infantry Division", "GroupID": "01B"})
        )
        (tmp_path / "2nd infantry.json").write_text(
            json.dumps({"group_name": "2nd Infantry", "GroupID": "01C"})
        )

        dupes = find_duplicate_groups(tmp_path)
        assert len(dupes) == 1  # 1st Infantry + 1st Infantry Division
        assert len(dupes[0]["people"]) == 2


class TestFindDuplicatePlaces:
    def test_get_coords_nested(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        data = {"coordinates": {"latitude": 49.18, "longitude": -0.37}}
        lat, lon = _get_coords(data)
        assert lat == 49.18
        assert lon == -0.37

    def test_get_coords_flat(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        data = {"latitude": 49.18, "longitude": -0.37}
        lat, lon = _get_coords(data)
        assert lat == 49.18

    def test_get_coords_missing(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        lat, lon = _get_coords({})
        assert lat is None

    def test_check_match_far_apart(self):
        from scripts.find_duplicate_places_v2 import _check_match

        p1 = {"name": "Caen", "lat": 49.18, "lon": -0.37}
        p2 = {"name": "Caen copy", "lat": 48.0, "lon": 2.0}  # ~300km away
        match, _ = _check_match(p1, p2)
        assert not match

    def test_check_match_nearby_similar(self):
        from scripts.find_duplicate_places_v2 import _check_match

        p1 = {"name": "Marseille", "lat": 43.3, "lon": 5.37}
        p2 = {"name": "Marseilles", "lat": 43.3, "lon": 5.37}
        match, reasons = _check_match(p1, p2)
        assert match


class TestEcsEntrypoint:
    def test_phase_names(self):
        with patch.dict(os.environ, {"S3_BUCKET": "test-bucket"}):
            from ecs_entrypoint import PHASE_NAMES, PHASE_SUFFIXES

            assert "phase1_parse.py" in PHASE_NAMES
            assert "phase2_extract.py" in PHASE_NAMES
            assert "phase3_enrich_data.py" in PHASE_NAMES
            assert "import_to_dynamodb.py" in PHASE_NAMES
            assert PHASE_SUFFIXES["phase1_parse.py"] == "phase1-parse"

    def test_read_manifest_empty(self):
        with patch.dict(os.environ, {"CACHE_TABLE": "test-table"}):
            from ecs_entrypoint import _read_manifest

            with patch("ecs_entrypoint.boto3") as mock_boto:
                mock_table = MagicMock()
                mock_table.get_item.return_value = {}
                mock_boto.resource.return_value.Table.return_value = mock_table
                result = _read_manifest()
                assert result == []
