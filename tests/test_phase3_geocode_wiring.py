"""C1 regression guard: Phase 3 must actually WIRE the geocoding cascade.

The geocoding subsystem was fully built + tested but never called by
`phase3_enrich_data.main()`, so every place stayed at lat/long=0.0 with empty
country. These tests assert the wiring exists so it can't silently regress to
dead code again. (The geocoder's own write behavior is covered by
test_places_grok_geocode.py — here we only guard the integration.)
"""

import ast
from pathlib import Path

PHASE3 = Path(__file__).resolve().parent.parent / "phase3_enrich_data.py"


def _source() -> str:
    return PHASE3.read_text(encoding="utf-8")


def test_phase3_calls_geocode_places_dir():
    """main() must invoke geocode_places_dir — the coordinate-writing entry point."""
    assert "geocode_places_dir(" in _source(), (
        "phase3_enrich_data must call geocode_places_dir; the geocoding cascade "
        "regressed to dead code (places would stay un-geocoded)."
    )


def test_phase3_builds_the_cascade():
    """It must compose the cascade (Nominatim -> hill -> Grok), not call one geocoder."""
    src = _source()
    assert "cascade_geocoder(" in src
    assert "make_nominatim_geocoder(" in src
    assert "make_hill_geocoder(" in src


def test_phase3_geocode_imports_resolve():
    """The geocoder symbols phase3 imports must actually exist (no stale import)."""
    from src.enrichment.hill_geocode import make_hill_geocoder  # noqa: F401
    from src.enrichment.nominatim_geocode import make_nominatim_geocoder  # noqa: F401
    from src.enrichment.places_grok_geocode import (  # noqa: F401
        cascade_geocoder,
        geocode_place,
        geocode_places_dir,
    )


def test_phase3_geocode_call_is_in_places_block_and_writes():
    """geocode_places_dir must be called with write=True (persist coordinates)."""
    src = _source()
    # the call site passes write=True
    tree = ast.parse(src)
    found_write = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "geocode_places_dir"
        ):
            for kw in node.keywords:
                if kw.arg == "write" and getattr(kw.value, "value", None) is True:
                    found_write = True
    assert found_write, "geocode_places_dir must be called with write=True"
