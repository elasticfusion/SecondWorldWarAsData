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


def build_image_entry(
    url: str, caption: str, book: str, source_section_id: Optional[str]
) -> dict:
    """Construct a schema-valid entity image entry with durable ULID attribution.

    Shared by the image->person and image->place linkers (byte-identical): {url, title (caption
    capped 300), source "document:<book>" or "document"} plus an optional SourceSectionID for
    durable provenance attribution.
    """
    entry: dict = {
        "url": url,
        "title": caption[:300],
        "source": f"document:{book}" if book else "document",
    }
    if source_section_id:
        entry["SourceSectionID"] = source_section_id  # durable ULID attribution
    return entry


def drain_enrichment_futures(futures, total: int, noun: str) -> int:
    """Drain a {future: file} map of enrichment futures, counting successes and logging progress
    every 10 (and at the end). Returns the enriched count.

    Shared by enrich_groups / enrich_places: the loop is identical apart from the log noun (e.g.
    "group"/"Groups", "place"/"Places"). ``noun`` is the singular label used in both the per-failure
    warning and the capitalized progress line.
    """
    import logging as _logging
    from concurrent.futures import as_completed

    log = _logging.getLogger(__name__)
    enriched = 0
    processed = 0
    for future in as_completed(futures):
        processed += 1
        try:
            if future.result():
                enriched += 1
        except Exception as e:  # noqa: BLE001 - one failure must not abort the drain
            log.warning("Failed to enrich %s %s: %s", noun, futures[future].stem, e)
        if processed % 10 == 0 or processed == total:
            log.info(
                "  %ss progress: %d/%d done (%d enriched)",
                noun.capitalize(),
                processed,
                total,
                enriched,
            )
    return enriched
