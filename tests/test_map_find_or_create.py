"""Step 4: URL-keyed find-or-create for maps + cross-chapter reference-footprint accumulation.
A roman-numeral campaign foldout (Map VIII) referenced from multiple chapters/books must resolve to
ONE Map record whose references[] ACCUMULATES each chapter's footprint (not a duplicate record, not
a dropped reference).
"""

import json
from pathlib import Path

import pytest

from src.extraction.map_common import (
    find_or_create_map_by_url,
    base_map_record,
    _dedup_references,
)

URL = "https://ibiblio.org/hyperwar/USA/USA-E-XChannel/maps/USA-E-XChannel-VIII.jpg"


def _build(map_id):
    return base_map_record(
        map_id=map_id,
        map_title="Map VIII",
        description="d",
        map_type=None,
        source_url=URL,
        local_path=None,
        local_image_path=None,
        file_format="jpg",
        storage_backend="filesystem",
    )


def _files(d):
    return list(Path(d).glob("*.json"))


def test_create_then_append_across_chapters(tmp_path):
    idx = {}
    f1 = find_or_create_map_by_url(
        url=URL,
        maps_dir=tmp_path,
        url_index=idx,
        references=[{"book": "XChannel", "paragraph_number": 39, "Sub_eventID": "S1"}],
        build_record=_build,
    )
    assert len(_files(tmp_path)) == 1
    # second chapter references the SAME map
    f2 = find_or_create_map_by_url(
        url=URL,
        maps_dir=tmp_path,
        url_index=idx,
        references=[{"book": "Breakout", "paragraph_number": 12, "Sub_eventID": "S9"}],
        build_record=_build,
    )
    assert f1 == f2  # same record, not a duplicate
    assert len(_files(tmp_path)) == 1
    rec = json.loads(f2.read_text())
    assert len(rec["references"]) == 2  # accumulated across chapters


def test_identical_reference_deduped(tmp_path):
    idx = {}
    ref = [{"book": "XChannel", "paragraph_number": 39, "Sub_eventID": "S1"}]
    find_or_create_map_by_url(
        url=URL, maps_dir=tmp_path, url_index=idx, references=ref, build_record=_build
    )
    f = find_or_create_map_by_url(
        url=URL, maps_dir=tmp_path, url_index=idx, references=ref, build_record=_build
    )
    assert len(json.loads(f.read_text())["references"]) == 1  # idempotent


def test_empty_url_guard(tmp_path):
    assert (
        find_or_create_map_by_url(
            url="", maps_dir=tmp_path, url_index={}, references=[], build_record=_build
        )
        is None
    )


def test_dedup_references_helper():
    existing = [{"book": "A", "paragraph_number": 1, "Sub_eventID": "S1"}]
    incoming = [
        {"book": "A", "paragraph_number": 1, "Sub_eventID": "S1"},  # dup
        {"book": "B", "paragraph_number": 2, "Sub_eventID": "S2"},  # new
    ]
    merged, added = _dedup_references(existing, incoming)
    assert added == 1 and len(merged) == 2
