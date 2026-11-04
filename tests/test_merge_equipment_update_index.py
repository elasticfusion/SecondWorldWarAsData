"""DRY P2.9 guard: the live merge_equipment script no longer defines its own update_index clone; it
routes to the canonical src.dedup.merge.update_index (prevents index-write drift between the script
and production dedup)."""

import ast
from pathlib import Path


def test_merge_equipment_has_no_local_update_index():
    src = Path("scripts/merge_equipment.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "update_index" not in top_defs  # the script-local clone is gone


def test_merge_equipment_imports_canonical_update_index():
    # Formatting-independent: assert via AST that update_index is imported from src.dedup.merge
    # (black may wrap the import across lines, so a source substring check would be brittle).
    src = Path("scripts/merge_equipment.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "src.dedup.merge"
        and any(a.name == "update_index" for a in n.names)
        for n in ast.walk(tree)
    )
    assert imported


def test_canonical_update_index_still_works(tmp_path):
    import json
    from src.dedup.merge import update_index

    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"old name": "a.json"}), encoding="utf-8")
    update_index(idx, "Old Name", "b.json")
    data = json.loads(idx.read_text(encoding="utf-8"))
    assert data["old name"] == "b.json"  # lowercased+stripped key repointed
