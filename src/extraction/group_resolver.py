"""Shared unit/group resolution: a unit designation -> GroupID via the canonical
`src/dedup/unit_key`. One implementation reused by casualties cross-referencing, map
unit resolution, and any feature that must link a unit name to a people_groups record.

Resolution is structural (number-set + service + arm + echelon), not string similarity, so
"358th Infantry" matches "358th Inf" / "358th Infantry Regiment" while vetoing genuine
differences. Unresolved -> None (never guessed). When several people_groups records share one
canonical key (unmerged duplicates), resolves to one deterministically; genuinely distinct
units remaining -> None.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.dedup.unit_key import UnitKey, derive_unit_key, unit_keys_match


def build_group_unitkey_index(groups_dir: Path) -> List[Tuple[str, str, UnitKey]]:
    """[(GroupID, name, UnitKey)] over all people_groups records."""
    idx: List[Tuple[str, str, UnitKey]] = []
    if not groups_dir.exists():
        return idx
    for gf in groups_dir.glob("*.json"):
        if gf.name in ("index.json", "duplicate_report.json", "not_duplicates.json"):
            continue
        try:
            d = json.loads(gf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        gid = d.get("GroupID")
        if not gid:
            continue
        name = d.get("name") or d.get("common_name") or d.get("group_name") or ""
        if name:
            idx.append((gid, name, derive_unit_key(name)))
    return idx


def resolve_group_id(name: str, index: List[Tuple[str, str, UnitKey]]) -> Optional[str]:
    """Resolve a unit designation to a GroupID via the canonical unit key, or None.

    Collapses unmerged duplicate records that share one canonical key (resolves to the
    lowest GroupID deterministically); returns None when genuinely distinct units remain
    (ambiguous) or nothing matches.
    """
    if not name:
        return None
    k = derive_unit_key(name)
    if not k.numbers:  # unnumbered/underspecified -> don't guess
        return None
    cand = [(gid, gk) for (gid, _nm, gk) in index if unit_keys_match(k, gk)[0]]
    gids = {gid for gid, _gk in cand}
    if len(gids) == 1:
        return next(iter(gids))
    if not gids:
        return None
    keys = {gk for _gid, gk in cand}
    if len(keys) == 1:  # all candidates are the SAME unit (dup records) -> pick one
        return sorted(gids)[0]
    return None  # genuinely distinct units remain -> ambiguous, don't guess
