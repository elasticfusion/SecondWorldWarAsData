"""Shared helpers for the one-off ``scripts/extract_*`` extractors.

Most extract scripts write their rows out with an identical CSV boilerplate (mkdir parents, open
utf-8 newline='', csv.DictWriter, header, rows). This is the single source of truth for that write.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional


def write_dict_csv(
    path: Path,
    fieldnames: List[str],
    rows: List[Dict[str, str]],
    *,
    extrasaction: Optional[str] = "ignore",
) -> None:
    """Write ``rows`` to ``path`` as a CSV with ``fieldnames`` header.

    Creates parent dirs, opens utf-8 with ``newline=""`` (correct csv line endings), and uses
    ``csv.DictWriter``. ``extrasaction`` defaults to ``"ignore"`` (drop unmodeled keys); pass
    ``None`` for DictWriter's strict default (raise on an extra key) to match a caller that relied
    on it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        if extrasaction is None:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
        else:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, extrasaction=extrasaction
            )
        writer.writeheader()
        writer.writerows(rows)
