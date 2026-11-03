"""Shared entity-extraction helpers — single source of truth for logic that was copy-pasted across
the per-entity extractors (people, people_groups, places, dates, weather_central).

Book/author/series provenance is stamped onto every extracted entity from the source document's
``-parsed.json`` sibling. Five extractors each hand-rolled this load with DIVERGENT guards (falsy
``if not parsed_data`` / no guard at all / the safe ``isinstance(dict)`` check), so a non-dict or
empty parsed file was handled three different ways — and a fix to one never reached the others.
This module is the one loader they all delegate to, with the SAFE guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple


def parsed_sibling(event_file: Path) -> Path:
    """Derive the ``-parsed.json`` sibling of an ``-event.json`` file (same directory)."""
    return event_file.parent / event_file.name.replace("-event.json", "-parsed.json")


def load_book_metadata(parsed_file: Optional[Path]) -> Tuple[str, str, str]:
    """Load ``(book, author, series)`` provenance from a ``-parsed.json`` file.

    Returns empty strings when the file is absent/empty/malformed (the caller fails loud on empty
    provenance). Uses the SAFE ``isinstance(dict)`` guard so a parsed file that is a list/scalar/
    null does not raise — the single consistent behavior for all entity extractors.
    """
    if not parsed_file or not parsed_file.exists():
        return "", "", ""
    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
    if not isinstance(parsed_data, dict):
        return "", "", ""
    return (
        parsed_data.get("book", ""),
        parsed_data.get("author", ""),
        parsed_data.get("series", ""),
    )


def load_json_index(index_file: Path) -> dict:
    """Load a JSON index file into a dict, or return ``{}`` if it does not exist.

    The single source of truth for the ``load index.json -> dict`` read that the per-entity
    extractors (dates, places, weather_central, people) each duplicated. Callers that hold the
    directory pass ``entity_dir / "index.json"``.
    """
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
