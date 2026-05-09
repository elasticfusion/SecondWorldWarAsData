"""Shared dedup report validation — ensures no ghost entries."""

from pathlib import Path
from typing import Dict, List


def validate_report_groups(groups: List[Dict], entity_dir: Path) -> List[Dict]:
    """Remove entries from groups whose files don't exist.

    Returns filtered groups with at least 2 members.
    """
    validated = []
    for group in groups:
        key = "people" if "people" in group else "groups"
        entries = [
            p
            for p in group.get(key, [])
            if (entity_dir / p.get("filename", "")).exists()
        ]
        if len(entries) >= 2:
            group[key] = entries
            validated.append(group)
    return validated
