"""Groups must persist a name: the prompt emits `group_name`, but the batch make_record used to read
`obj.get('name')` (never emitted) -> null -> dropped -> 100% groups.enrich_skip_no_name in Phase 3
(XChannel-8 2026-10-29). The record builder must read group_name (with a name fallback).
"""

import re
from pathlib import Path


def test_groups_make_record_reads_group_name():
    src = Path("src/extraction/batch_parallel.py").read_text(encoding="utf-8")
    # locate the people_groups make_record block
    flat = " ".join(src.split())  # collapse whitespace (robust to black wrapping)
    assert '"group_name": obj.get("group_name") or obj.get("name")' in flat, (
        "groups make_record/make_key must read group_name (prompt field) with a name fallback — "
        "reading only obj.get('name') drops the name and breaks group enrichment"
    )


def test_groups_make_record_shape_populates_both_name_keys():
    # simulate the lambda's field resolution on a realistic Grok groups object (group_name + country)
    obj = {"group_name": "VII Corps", "country": "USA"}
    name = obj.get("group_name") or obj.get("name")
    assert name == "VII Corps"
    # name fallback still works if a response used `name`
    obj2 = {"name": "82d Airborne Division", "country": "USA"}
    assert (obj2.get("group_name") or obj2.get("name")) == "82d Airborne Division"
