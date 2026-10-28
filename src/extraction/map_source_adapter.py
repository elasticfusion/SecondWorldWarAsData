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

# Cap on the vision description length written into the source (P0-review hardening).
_MAX_DESC_CHARS = 8000


def _safe_map_id(map_id: str) -> str:
    """Bound + sanitize a map id to a safe path/book token (P0-review hardening)."""
    import re

    tok = re.sub(r"[^A-Za-z0-9._-]+", "", str(map_id or ""))
    return tok[:40] or "unknown"


def render_map_markdown(
    map_title: str, depicted: Dict[str, Any], source_url: str = ""
) -> str:
    """Render an approved map's vision ``depicted`` block into a RAW markdown SOURCE body — the same
    kind of content a book chapter supplies to Phase-1 parse. Returns "" if nothing groundable.

    This is the map-as-source body: Phase 1 parses it into paragraphs, Phase 2 derives the event.
    """
    text = _render_depicted_text(depicted)
    if not text:
        return ""
    title = (map_title or "WWII map").strip()
    body = text[:_MAX_DESC_CHARS]
    lines = [f"## {title}", ""]
    if source_url:
        lines.append(f"(Source map: {source_url})")
        lines.append("")
    lines.append(body)
    return "\n".join(lines).rstrip() + "\n"


def render_map_meta_yaml(map_id: str, map_title: str, source_url: str = "") -> str:
    """Render the ``-meta.yaml`` sidecar for a map source (mirrors a book chapter's meta)."""
    import yaml

    meta = {
        "series": "External maps (vision-derived)",
        "book": f"map-{_safe_map_id(map_id)}",
        "author": "",
        "chapter_number": 1,
        "chapter_title": (map_title or f"Map {_safe_map_id(map_id)}")[:300],
        "license": "Unknown",
        "copyright_date": "",
        "source_url": source_url or "",
        "source_type": MAP_SOURCE_TYPE,
    }
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)


# Below this vision-reported legibility confidence, a map is too illegible to derive a trustworthy
# event from — the shared map-as-source choke point skips it (M7: so a low-quality inline map does
# not inject low-confidence events). This is the map-wide confidence gate the spec deferred. A map
# whose vision result omits the field (older prompt / external callers) is NOT gated — only an
# explicit, confidently-low score is.
_MIN_LEGIBILITY_CONFIDENCE = 0.35


def _legibility_confidence(depicted: Dict[str, Any]) -> Optional[float]:
    """The vision-reported legibility_confidence (0.0-1.0) if present + parseable, else None."""
    if not isinstance(depicted, dict) or "legibility_confidence" not in depicted:
        return None
    raw = depicted.get("legibility_confidence")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError, OverflowError):
        return None


def _render_depicted_text(depicted: Dict[str, Any]) -> str:
    """Render the vision ``depicted`` block into factual prose paragraphs for event extraction.

    Grounded ONLY in the provided fields (the vision step is instructed to include only legibly-shown
    content). Empty/absent fields are skipped — never fabricated. A map whose vision legibility
    confidence is explicitly below ``_MIN_LEGIBILITY_CONFIDENCE`` renders to "" (the shared M7 gate:
    too illegible to trust — skip rather than inject a low-confidence event).
    """
    if not isinstance(depicted, dict):
        return ""
    conf = _legibility_confidence(depicted)
    if conf is not None and conf < _MIN_LEGIBILITY_CONFIDENCE:
        logger.info(
            "OBSERV map_source.skipped reason=low_legibility confidence=%.2f threshold=%.2f",
            conf,
            _MIN_LEGIBILITY_CONFIDENCE,
        )
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
            "OBSERV map_source.built map=%s paragraphs=1 units=%d places=%d legibility=%s",
            map_id,
            len(_clean_str_list(depicted.get("units"))),
            len(_clean_str_list(depicted.get("places"))),
            (
                f"{_legibility_confidence(depicted):.2f}"
                if _legibility_confidence(depicted) is not None
                else "n/a"
            ),
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


def recycle_map_as_source(
    *, map_id: str, map_title: str, source_url: str, depicted: Any
) -> bool:
    """SHARED map-as-source recycle: write a vision-approved map's ``depicted`` as a RAW SOURCE pair
    (meta.yaml + content.md) under ``contentrepository/map-<id>/`` so it re-enters the normal
    pipeline (Phase 1 parse -> Phase 2 event -> Phase 3 enrich), exactly like a book chapter
    (spec §3.1 Option B). The SINGLE writer used by BOTH map discovery front-ends — the OpenSERP
    search path (openserp_maps) and the in-document map-link path (map_link_source) — so the write +
    its OBSERV live in one place. Fail-safe: never raises; returns True iff a source was written.
    """
    from pathlib import Path as _Path

    try:
        if not isinstance(depicted, dict) or not depicted:
            logger.info(
                "OBSERV map_source.skipped map=%s reason=no_depicted "
                "(catalog record only, no event derivation)",
                map_id,
            )
            return False
        md = render_map_markdown(map_title, depicted, source_url=source_url)
        if not md:
            logger.info(
                "OBSERV map_source.skipped map=%s reason=empty_depicted", map_id
            )
            return False
        safe_id = _safe_map_id(map_id)
        base = _Path("contentrepository") / f"map-{safe_id}"
        base.mkdir(parents=True, exist_ok=True)
        # Mirror a book chapter's source pair so Phase-1 parse consumes it unchanged.
        (base / f"map-{safe_id}-meta.yaml").write_text(
            render_map_meta_yaml(map_id, map_title, source_url=source_url),
            encoding="utf-8",
        )
        (base / f"map-{safe_id}a-content.md").write_text(md, encoding="utf-8")
        logger.info(
            "OBSERV map_source.recycled map=%s source=contentrepository/map-%s/ "
            "(re-enters Phase 1->2->3 for event derivation)",
            map_id,
            safe_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 - recycle must never crash the caller
        logger.warning("OBSERV map_source.error map=%s reason=%s", map_id, str(e)[:120])
        return False
