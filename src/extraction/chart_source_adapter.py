"""Chart-as-source-document adapter (M6, multimodal epic).

Analogous to ``map_source_adapter`` (docs/current/design/MAP_AS_SOURCE_DOCUMENT.md): a vision-read
organization / command-structure chart (e.g. the naval command chart at fn23) becomes a SOURCE that
flows through the normal pipeline — Phase-1 parse -> Phase-2 event -> people_groups + command
relationships via the shared ``event_mentions`` spine — with NO chart-specific pipeline logic.

Owner requirements (2026-10-27): the chart-derived entities must carry **ULID tracking back to the
ORIGINAL event** (the EventID/SourceSectionID of the chapter where the chart was cited), everything
flows through the event spine, everything is observable, and the implementation reuses the proven
map-as-source machinery rather than a parallel path.

PURE + fail-safe: this module only BUILDS the parsed-source + renders the recycle pair. It does not
fetch, call vision, or trigger a run (wiring is the caller's job).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# source_type marker so downstream consumers + validation distinguish CHART-DERIVED (vision-inferred
# command structure) events from prose-stated ones. Mirrors map_source_adapter.MAP_SOURCE_TYPE.
CHART_SOURCE_TYPE = "chart"

_MAX_DESC_CHARS = 8000


def _safe_chart_id(chart_id: str) -> str:
    """Bound + sanitize a chart id to a safe path/book token (mirrors _safe_map_id)."""
    tok = re.sub(r"[^A-Za-z0-9._-]+", "", str(chart_id or ""))
    return tok[:40] or "unknown"


def _clean_str_list(value: Any) -> List[str]:
    """Non-empty trimmed strings from a list value (fail-safe: [] for non-list/None)."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _render_command_edges(edges: Any) -> str:
    """Render the who-commands-whom edges into factual prose ("X commands Y."). Grounded only in the
    provided {superior, subordinate} pairs; skips malformed entries. Fail-safe -> ""."""
    if not isinstance(edges, list):
        return ""
    lines: List[str] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        sup = (e.get("superior") or "").strip()
        sub = (e.get("subordinate") or "").strip()
        if sup and sub:
            lines.append(f"{sup} commands {sub}.")
    return " ".join(lines)


def _render_chart_text(depicted: Dict[str, Any]) -> str:
    """Render the vision ``depicted`` command-structure block into factual prose for event
    extraction. Grounded ONLY in provided fields; empty/absent fields skipped (never fabricated).
    """
    if not isinstance(depicted, dict):
        return ""
    parts: List[str] = []
    desc = (depicted.get("description") or "").strip()
    if desc:
        parts.append(desc)
    title = (depicted.get("organization") or "").strip()
    if title:
        parts.append(f"Organization/command depicted: {title}.")
    for field, prefix in (
        ("units", "Units/formations shown: "),
        ("commanders", "Commanders named: "),
    ):
        vals = _clean_str_list(depicted.get(field))
        if vals:
            parts.append(prefix + "; ".join(vals) + ".")
    edges = _render_command_edges(depicted.get("command_edges"))
    if edges:
        parts.append("Command relationships: " + edges)
    return "\n\n".join(parts).strip()


def _provenance(
    chart_id: str,
    source_url: str,
    origin_event_id: str,
    origin_source_section_id: str,
) -> Dict[str, Any]:
    """The _chart_source provenance block — carries ULID tracking back to the ORIGINAL event +
    source_section the chart was cited in (owner requirement)."""
    return {
        "chart_id": chart_id,
        "source_url": source_url,
        "acquisition_method": "chart_vision",
        # ULID tracking to the original event/source the chart was found in:
        "origin_EventID": origin_event_id or None,
        "origin_SourceSectionID": origin_source_section_id or None,
    }


def build_chart_parsed_source(
    *,
    chart_id: str,
    chart_title: str,
    approved_image_url: str,
    depicted: Dict[str, Any],
    source_url: str = "",
    author: str = "",
    origin_event_id: str = "",
    origin_source_section_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Build the parsed-source dict for a vision-approved command chart, in the
    ``create_event_prompt`` contract. Returns None if there is no usable depicted command structure
    (do NOT fabricate an event). Carries ULID tracking to the original event. Fail-safe.
    """
    try:
        text = _render_chart_text(depicted)
        if not text:
            logger.info(
                "OBSERV chart_source.skipped chart=%s reason=empty_depicted", chart_id
            )
            return None
        title = (chart_title or f"Chart {_safe_chart_id(chart_id)}")[:300]
        images = (
            [{"type": "external", "url": approved_image_url, "alt_text": title}]
            if approved_image_url
            else []
        )
        parsed = {
            "book": f"chart-{_safe_chart_id(chart_id)}",
            "chapter_number": 1,
            "chapter_title": title,
            "author": author or "",
            "source_type": CHART_SOURCE_TYPE,
            "paragraphs": [{"absolute_number": 1, "text": text[:_MAX_DESC_CHARS]}],
            "images": images,
            "_chart_source": _provenance(
                _safe_chart_id(chart_id),
                source_url,
                origin_event_id,
                origin_source_section_id,
            ),
        }
        logger.info(
            "OBSERV chart_source.built chart=%s commanders=%d edges=%d origin_event=%s",
            chart_id,
            len(_clean_str_list(depicted.get("commanders"))),
            (
                len(depicted.get("command_edges") or [])
                if isinstance(depicted.get("command_edges"), list)
                else 0
            ),
            origin_event_id or "-",
        )
        return parsed
    except Exception as e:  # noqa: BLE001 - adapter must never crash the chart path
        logger.warning(
            "OBSERV chart_source.error chart=%s reason=%s", chart_id, str(e)[:120]
        )
        return None


def render_chart_markdown(
    chart_title: str, depicted: Dict[str, Any], source_url: str = ""
) -> str:
    """Render an approved chart's vision ``depicted`` block into a RAW markdown SOURCE body (the same
    kind of content a book chapter supplies to Phase-1 parse). "" if nothing groundable.
    """
    text = _render_chart_text(depicted)
    if not text:
        return ""
    title = (chart_title or "WWII command chart").strip()
    lines = [f"## {title}", ""]
    if source_url:
        lines.append(f"(Source chart: {source_url})")
        lines.append("")
    lines.append(text[:_MAX_DESC_CHARS])
    return "\n".join(lines).rstrip() + "\n"


def render_chart_meta_yaml(
    chart_id: str,
    chart_title: str,
    source_url: str = "",
    origin_event_id: str = "",
    origin_source_section_id: str = "",
) -> str:
    """Render the ``-meta.yaml`` sidecar for a chart source (mirrors a book chapter's meta), carrying
    the ULID tracking to the original event/source_section."""
    import yaml

    meta = {
        "series": "Command charts (vision-derived)",
        "book": f"chart-{_safe_chart_id(chart_id)}",
        "author": "",
        "chapter_number": 1,
        "chapter_title": (chart_title or f"Chart {_safe_chart_id(chart_id)}")[:300],
        "license": "Unknown",
        "copyright_date": "",
        "source_url": source_url or "",
        "source_type": CHART_SOURCE_TYPE,
        "origin_EventID": origin_event_id or "",
        "origin_SourceSectionID": origin_source_section_id or "",
    }
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)


def recycle_chart_as_source(
    *,
    chart_id: str,
    chart_title: str,
    source_url: str,
    depicted: Any,
    origin_event_id: str = "",
    origin_source_section_id: str = "",
) -> bool:
    """Write a vision-approved command chart's ``depicted`` as a RAW SOURCE pair (meta.yaml +
    content.md) under ``contentrepository/chart-<id>/`` so it re-enters the normal pipeline (Phase 1
    parse -> Phase 2 event -> Phase 3 enrich) exactly like a book chapter, carrying ULID tracking to
    the original event. Fail-safe; returns True iff a source was written."""
    try:
        if not isinstance(depicted, dict) or not depicted:
            logger.info(
                "OBSERV chart_source.skipped chart=%s reason=no_depicted", chart_id
            )
            return False
        md = render_chart_markdown(chart_title, depicted, source_url=source_url)
        if not md:
            logger.info(
                "OBSERV chart_source.skipped chart=%s reason=empty_depicted", chart_id
            )
            return False
        safe_id = _safe_chart_id(chart_id)
        base = Path("contentrepository") / f"chart-{safe_id}"
        base.mkdir(parents=True, exist_ok=True)
        base.joinpath(f"chart-{safe_id}-meta.yaml").write_text(
            render_chart_meta_yaml(
                chart_id,
                chart_title,
                source_url=source_url,
                origin_event_id=origin_event_id,
                origin_source_section_id=origin_source_section_id,
            ),
            encoding="utf-8",
        )
        base.joinpath(f"chart-{safe_id}a-content.md").write_text(md, encoding="utf-8")
        logger.info(
            "OBSERV chart_source.recycled chart=%s source=contentrepository/chart-%s/ "
            "origin_event=%s origin_section=%s (re-enters Phase 1->2->3)",
            chart_id,
            safe_id,
            origin_event_id or "-",
            origin_source_section_id or "-",
        )
        return True
    except Exception as e:  # noqa: BLE001 - recycle must never crash the caller
        logger.warning(
            "OBSERV chart_source.error chart=%s reason=%s", chart_id, str(e)[:120]
        )
        return False


def _chart_vision_read(
    image_bytes: bytes, event_context: str, title: str, grok_client: Any
) -> Dict[str, Any]:
    """Run the externalized chart_vision prompt over a chart image. Returns the ``depicted`` command
    structure dict, or {} when not a relevant chart / on any failure (fail-safe, never fabricates).
    """
    try:
        import base64

        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "chart_vision", event_context=event_context or "", title=title or ""
        )
        b64 = base64.b64encode(image_bytes).decode("ascii")
        result = grok_client.extract_json_with_image_base64(
            prompt=prompt, image_base64=b64, cache_type="default"
        )
        if not isinstance(result, dict) or not result.get("is_relevant"):
            logger.info(
                "OBSERV chart_source.vision_rejected title=%s", (title or "")[:60]
            )
            return {}
        depicted = result.get("depicted")
        return depicted if isinstance(depicted, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV chart_source.vision_error reason=%s", str(e)[:120])
        return {}


def _resolve_origin_ulids(fetch_event: Dict[str, Any], out: Optional[Path]) -> tuple:
    """Origin (EventID, SourceSectionID) for an affirmed chart link-asset event: prefer ULIDs carried
    in the event's request_params (M4 backfill); else resolve the source book -> SourceSectionID
    (reusing the M5 book->section index). Fail-safe -> ("","")."""
    params = fetch_event.get("request_params") or {}
    event_id = str(params.get("origin_EventID") or "")
    section_id = str(params.get("origin_SourceSectionID") or "")
    if section_id or not out:
        return (event_id, section_id)
    try:
        from src.extraction.image_place_link import _build_book_section_index

        book = str(params.get("source_doc") or "")
        if book:
            section_id = _build_book_section_index(out).get(book.strip().lower(), "")
    except Exception:  # noqa: BLE001
        pass
    return (event_id, section_id)


def ingest_affirmed_chart(
    fetch_event: Dict[str, Any],
    image_bytes: bytes,
    *,
    grok_client: Any,
    out: Optional[Path] = None,
) -> bool:
    """Promote an AFFIRMED chart-image link-asset into a chart-as-source: vision-read the command
    structure, then recycle it into the pipeline carrying ULID tracking to the ORIGINAL event /
    source_section. Returns True iff a chart source was written. Fail-safe (never raises).
    """
    try:
        params = fetch_event.get("request_params") or {}
        endpoint = str(fetch_event.get("endpoint") or "")
        basename = endpoint.rsplit("/", 1)[-1].rsplit(".", 1)[
            0
        ]  # drop any file extension
        chart_id = _safe_chart_id(basename or fetch_event.get("fetch_event_id", ""))
        title = str(params.get("link_text") or "WWII command chart")
        source_url = str(params.get("source_url") or endpoint)
        depicted = _chart_vision_read(
            image_bytes, params.get("source_doc", ""), title, grok_client
        )
        if not depicted:
            return False
        origin_event_id, origin_section_id = _resolve_origin_ulids(fetch_event, out)
        return recycle_chart_as_source(
            chart_id=chart_id,
            chart_title=title,
            source_url=source_url,
            depicted=depicted,
            origin_event_id=origin_event_id,
            origin_source_section_id=origin_section_id,
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - ingestion must never crash the affirm action
        logger.warning("OBSERV chart_source.error reason=%s", str(e)[:120])
        return False
