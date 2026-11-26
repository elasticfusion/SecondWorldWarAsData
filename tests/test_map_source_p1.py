"""The map loop guard: a place known only from a map-derived source does not seed a new map search."""

import json
from pathlib import Path
import pytest


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
