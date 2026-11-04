"""Test the dead-module reachability gate (scripts/check_unreachable_modules.py) — the durable guard
for the P1.1 never-imported-module class that vulture can't catch."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_unreachable_modules",
    Path(__file__).resolve().parent.parent / "scripts" / "check_unreachable_modules.py",
)
chk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chk)


def test_current_tree_is_clean():
    # main must be clean (s3_lazy deleted; types allowlisted; entity_store/openserp_client cleared
    # by string-ref scan). If this fails, a NEW unreachable module was introduced.
    assert chk.main() == 0


def test_entry_point_detection():
    # __init__/__main__ and lambda_handlers.* are entry points (never flagged).
    assert chk._is_entry_point(Path("src/utils/__init__.py"), "src.utils.__init__")
    assert chk._is_entry_point(
        Path("lambda_handlers/phase1_handler.py"), "lambda_handlers.phase1_handler"
    )
    assert not chk._is_entry_point(Path("src/utils/geo.py"), "src.utils.geo")


def test_string_dotted_refs_counted():
    # The import scan must register dotted PREFIXES of string literals (patch("a.b.c")) so a
    # test-only-patched module counts as reachable. entity_store is such a case on main.
    imported = chk._imported_module_keys()
    assert (
        "src.utils.entity_store" in imported
    )  # referenced via test patch("...") strings
    assert "src.utils.geo" in imported  # normal import
