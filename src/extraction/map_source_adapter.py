"""Map-as-source-document adapter (P0).

Renders a VISION-APPROVED map into the SAME ``parsed.json`` contract that
``src.extraction.events.create_event_prompt`` consumes for book chapters — so a map enters the
normal Phase-2 extraction and an EVENT (plus people_groups / places / dates via the shared
event_mentions spine) is derived from it with NO map-specific pipeline logic.

See docs/current/design/MAP_AS_SOURCE_DOCUMENT.md. This module is PURE + fail-safe: it only BUILDS
the parsed-source + SourceMetadata from an already-approved map + its vision ``depicted`` block. It
does NOT fetch, write to S3, or trigger a run — wiring into import_openserp_maps is P1.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The source_type marker so downstream consumers + validation can distinguish MAP-DERIVED
# (vision-inferred) events from prose-stated ones (spec §4).
MAP_SOURCE_TYPE = "map"


def _render_depicted_text(depicted: Dict[str, Any]) -> str:
    """Render the vision ``depicted`` block into factual prose paragraphs for event extraction.

    Grounded ONLY in the provided fields (the vision step is instructed to include only legibly-shown
    content). Empty/absent fields are skipped — never fabricated.
    """
    if not isinstance(depicted, dict):
        return ""
    parts: List[str] = []
    desc = (depicted.get("description") or "").strip()
    if desc:
        parts.append(desc)
    ctx = _scalar_context(depicted)
    if ctx:
        parts.append(ctx)
    # (field, prefix) for the list-valued depicted fields, rendered uniformly.
    for field, prefix in (
        ("units", "Units depicted on the map: "),
        ("places", "Places/features labeled: "),
        ("movements", "Movements shown: "),
    ):
        vals = _clean_str_list(depicted.get(field))
        if vals:
            parts.append(prefix + "; ".join(vals) + ".")
    return "\n\n".join(parts).strip()


def _clean_str_list(value: Any) -> List[str]:
    """Non-empty trimmed strings from a list value (fail-safe: [] for non-list/None)."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _scalar_context(depicted: Dict[str, Any]) -> str:
    """Render the scalar context fields (operation / sector / date window) into one sentence group."""
    ctx: List[str] = []
    for field, label in (
        ("operation", "Operation"),
        ("front_line_or_sector", "Sector/front shown"),
        ("date_or_window", "Date depicted"),
    ):
        val = (depicted.get(field) or "").strip()
        if val:
            ctx.append(f"{label}: {val}.")
    return " ".join(ctx)


def build_map_parsed_source(
    *,
    map_id: str,
    map_title: str,
    approved_image_url: str,
    depicted: Dict[str, Any],
    source_url: str = "",
    author: str = "",
) -> Optional[Dict[str, Any]]:
    """Build the parsed-source dict for a vision-approved map.

    Returns a dict in the ``create_event_prompt`` contract
    (``{book, chapter_title, chapter_number, author, source_type, paragraphs, images}``), or None if
    there is no usable depicted text (nothing to extract an event from — do NOT fabricate one).
    Fail-safe: never raises.
    """
    try:
        text = _render_depicted_text(depicted)
        if not text:
            logger.info(
                "OBSERV map_source.skipped map=%s reason=empty_depicted "
                "(no legible content to derive an event)",
                map_id,
            )
            return None
        parsed = _assemble_parsed(
            map_id, map_title, approved_image_url, author, text, source_url
        )
        logger.info(
            "OBSERV map_source.built map=%s paragraphs=1 units=%d places=%d",
            map_id,
            len(_clean_str_list(depicted.get("units"))),
            len(_clean_str_list(depicted.get("places"))),
        )
        return parsed
    except Exception as e:  # noqa: BLE001 - adapter must never crash the map path
        logger.warning("OBSERV map_source.error map=%s reason=%s", map_id, str(e)[:120])
        return None


def _assemble_parsed(
    map_id: str,
    map_title: str,
    approved_image_url: str,
    author: str,
    text: str,
    source_url: str,
) -> Dict[str, Any]:
    """Assemble the parsed-source dict in the create_event_prompt contract."""
    title = (map_title or f"Map {map_id}")[:300]
    images = (
        [{"type": "external", "url": approved_image_url, "alt_text": title}]
        if approved_image_url
        else []
    )
    return {
        "book": f"map-{map_id}",
        "chapter_number": 1,
        "chapter_title": title,
        "author": author or "",
        "source_type": MAP_SOURCE_TYPE,
        "paragraphs": [{"absolute_number": 1, "text": text}],
        "images": images,
        "_map_source": {
            "map_id": map_id,
            "source_url": source_url,
            "acquisition_method": "map_vision",
        },
    }


def build_map_source_metadata(
    *,
    map_id: str,
    approved_image_url: str,
    source_url: str = "",
    capture_date: str = "",
):
    """Build a SourceMetadata for a map-as-source (acquisition_method='map_vision'). Fail-safe."""
    try:
        from pathlib import Path

        from src.ingestion.source_metadata import SourceMetadata

        return SourceMetadata(
            source_id=f"map-{map_id}",
            original_path=Path(approved_image_url or f"map-{map_id}"),
            media_type="image",
            acquisition_method="map_vision",
            acquisition_url=source_url or approved_image_url,
            capture_date=capture_date or None,
            notes=(
                "Vision-verified map promoted as a source document; its vision-derived description "
                "is the parsed text Phase-2 extracts an event from "
                "(docs/current/design/MAP_AS_SOURCE_DOCUMENT.md)."
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("build_map_source_metadata failed for %s: %s", map_id, e)
        return None
