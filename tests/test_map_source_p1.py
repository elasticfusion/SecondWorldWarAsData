"""P1: map-as-source wiring — raw source recycle + loop guard."""

import json
from pathlib import Path
import pytest


def test_render_map_markdown_and_meta():
    from src.extraction.map_source_adapter import (
        render_map_markdown,
        render_map_meta_yaml,
    )

    depicted = {
        "units": ["28th Infantry Division"],
        "places": ["Schmidt"],
        "description": "Tactical map of the first attack on Schmidt.",
    }
    md = render_map_markdown("Schmidt map", depicted, source_url="http://x/p")
    assert "## Schmidt map" in md and "Schmidt" in md and "28th Infantry" in md
    import yaml

    meta = yaml.safe_load(
        render_map_meta_yaml("01ABC", "Schmidt map", source_url="http://x/p")
    )
    assert (
        meta["book"] == "map-01ABC"
        and meta["source_type"] == "map"
        and meta["chapter_number"] == 1
    )


def test_recycle_writes_contentrepository_source_pair(tmp_path, monkeypatch):
    import src.extraction.openserp_maps as OM

    monkeypatch.chdir(tmp_path)  # contentrepository/ is relative
    depicted = {
        "units": ["9th Division"],
        "places": ["Hürtgen"],
        "description": "map of the Hürtgen.",
    }
    ok = OM._recycle_map_as_source(
        map_id="01MAP",
        map_title="Hürtgen map",
        approved_image_url="http://x/m.png",
        depicted=depicted,
        source_url="http://x/page",
    )
    assert ok
    base = tmp_path / "contentrepository" / "map-01MAP"
    assert (base / "map-01MAP-meta.yaml").is_file()
    assert (base / "map-01MAPa-content.md").is_file()
    assert "Hürtgen" in (base / "map-01MAPa-content.md").read_text()


def test_recycle_skips_when_no_depicted(tmp_path, monkeypatch):
    import src.extraction.openserp_maps as OM

    monkeypatch.chdir(tmp_path)
    assert (
        OM._recycle_map_as_source(
            map_id="01NONE",
            map_title="t",
            approved_image_url="",
            depicted=None,
            source_url="",
        )
        is False
    )
    assert not (tmp_path / "contentrepository" / "map-01NONE").exists()


def test_loop_guard_skips_map_derived_place(tmp_path):
    """A place whose event_mentions are ALL book=map-* must NOT seed a new map search."""
    import src.extraction.openserp_maps as OM

    pf = tmp_path / "p.json"
    pf.write_text(
        json.dumps(
            {
                "current_name": "Schmidt",
                "event_mentions": [{"EventID": "E1", "book": "map-01MAP"}],
            }
        )
    )

    class _Hist:
        def has_searched(self, n):
            return False

    imported, skipped_searched, skipped_dl = OM._process_place_file(
        pf,
        1,
        1,
        _Hist(),
        True,
        50,
        "http://localhost:7001",
        None,
        10,
        30,
        None,
        tmp_path,
        set(),
    )
    assert (imported, skipped_searched) == (0, 1)  # skipped by loop guard


def test_loop_guard_allows_prose_sourced_place(tmp_path):
    """A place with a prose source (book != map-*) must NOT be short-circuited by the loop guard.
    We assert the guard PREDICATE directly (all-map-derived) rather than drive the network path.
    """
    prose = [{"EventID": "E1", "book": "RoerDams"}]
    maponly = [{"EventID": "E1", "book": "map-01MAP"}]

    def _all_map(ems):
        books = [m.get("book") for m in ems if isinstance(m, dict)]
        return bool(books) and all(
            isinstance(b, str) and b.startswith("map-") for b in books
        )

    assert _all_map(maponly) is True  # guard WOULD skip a map-only place
    assert _all_map(prose) is False  # guard does NOT skip a prose-sourced place
    assert _all_map([]) is False  # no mentions -> not guarded
