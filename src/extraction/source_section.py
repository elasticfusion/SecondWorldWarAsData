"""Producer for the ``source_section`` entity (output/source_section/*.json).

A ``source_section`` is the coarse-grained, source-neutral anchor above the granular
Event/Sub-event layer — one record per top-level source section (book chapter / article /
report / KTB entry). This module is the SINGLE writer for the entity: it synthesizes the
section summary, links the per-section ``EventID``, and (in later phases) carries the derived
operation label + Grokipedia/Wikipedia article enrichment. All writes go through the native
version-stamped path.

Produced in PHASE 2 (after events extraction, which supplies the Event + aggregated
sub-event signal). The operation derivation + Grok/Wiki fetch (also Phase 2) are added in
subsequent increments.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import logging

import ulid

from src.schemas import entity_version, inject_metadata
from src.utils.file_lock import write_json_with_lock

# This module is the contract writer for the source_section entity: the version of the
# schema it targets must equal the entity's current version (enforced by the target guard).
SCHEMA_TARGET = entity_version("source_section")

ENTITY = "source_section"

logger = logging.getLogger(__name__)


def _coerce_section_title(chapter: Any) -> Optional[str]:
    """The source-section title comes from the event file's ``Chapter`` field, which may be a
    plain string or an object (title + metadata). Return a plain title string or None.
    """
    if isinstance(chapter, str):
        return chapter or None
    if isinstance(chapter, dict):
        title = chapter.get("title") or chapter.get("name")
        return title or None
    return None


def build_source_section(
    event_data: Dict[str, Any],
    source: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build one source_section record from a finalized event file.

    One Event per source section (events.py merges chunk-split chapters into a single Event),
    so this is a 1:1 anchor. section_summary is left null here — it is synthesized together
    with the operation label in the Phase-2 LLM pass (a later increment), per null-over-fake.
    """
    event = event_data.get("Event") or {}
    event_id = event.get("EventID")
    if not event_id:
        return None
    record = {
        "SourceSectionID": str(ulid.new()),
        "section_title": _coerce_section_title(event_data.get("Chapter")),
        "section_summary": None,  # synthesized with the operation label in the LLM pass
        "source": source,
        "EventID": event_id,
        "operation": None,
        "reference_articles": None,
        "wikipedia_checked_at": None,
    }
    return record


def _save_source_section(output_dir: Path, record: Dict[str, Any]) -> Optional[Path]:
    """Write one source_section record via the native, version-stamped locked writer."""
    section_id = record.get("SourceSectionID")
    if not section_id:
        return None
    section_dir = output_dir / "source_section"
    section_dir.mkdir(parents=True, exist_ok=True)
    path = section_dir / f"{section_id}.json"
    inject_metadata(record, entity=ENTITY)
    write_json_with_lock(path, record, entity=ENTITY)
    return path


def emit_source_section(
    event_data: Dict[str, Any],
    output_dir: Path,
    source: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Build + natively commit a source_section record for a finalized event. Fail-safe:
    returns None (and does not raise) if the event lacks an EventID."""
    record = build_source_section(event_data, source=source)
    if record is None:
        return None
    return _save_source_section(output_dir, record)


# --- Step 3: combined summary + operation derivation (separate Phase-2 pass) --------------

_MAX_SUBEVENTS_INLINE = (
    60  # above this, roll up before sending (avoid oversized prompt)
)
_DEFAULT_CONFIDENCE_THRESHOLD = 0.6


def _truncate_to_two_sentences(text: Optional[str]) -> Optional[str]:
    """Enforce the <2-sentence constraint defensively, in case the model over-produces."""
    if not text:
        return text
    import re

    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:2]).strip() or None


def gather_section_signal(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Collect the CUMULATIVE-SUMMARY signal for an event section: title, event name, the
    distilled sub-event summaries, and aggregated place/date references. Does NOT read the
    raw chapter (cheaper, no token-limit risk). Rolls up sub-events if the list is oversized.
    """
    event = event_data.get("Event") or {}
    subs = event.get("Sub-events") or []
    summaries = [s.get("Sub-event_summary") for s in subs if s.get("Sub-event_summary")]
    if len(summaries) > _MAX_SUBEVENTS_INLINE:
        # Rolling reduction: keep head + tail so the prompt stays bounded but representative.
        head = summaries[: _MAX_SUBEVENTS_INLINE // 2]
        tail = summaries[-_MAX_SUBEVENTS_INLINE // 2 :]
        summaries = head + ["…"] + tail
    places: list = []
    dates: list = []
    for s in subs:
        for p in s.get("places") or []:
            if p not in places:
                places.append(p)
        for d in s.get("dates") or []:
            if d not in dates:
                dates.append(d)
    return {
        "section_title": _coerce_section_title(event_data.get("Chapter")),
        "event_name": event.get("Event_Name"),
        "sub_event_summaries": summaries,
        "places": places,
        "dates": dates,
    }


def _normalize_operation(op: Any, threshold: float) -> Optional[Dict[str, Any]]:
    """Apply null-over-fake: a missing op, missing name, or sub-threshold confidence -> None."""
    if not isinstance(op, dict):
        return None
    name = op.get("name")
    conf = op.get("confidence")
    if not name:
        return None
    try:
        if conf is None or float(conf) < threshold:
            return None
    except (TypeError, ValueError):
        return None
    return {
        "name": name,
        "wikipedia_title": op.get("wikipedia_title"),
        "aliases": op.get("aliases") or [],
        "confidence": float(conf),
        "source": "derived",
    }


def derive_summary_and_operation(
    record: Dict[str, Any],
    event_data: Dict[str, Any],
    grok_client: Any,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
) -> bool:
    """One LLM call over the cumulative summaries -> section_summary (<2 sentences) + operation
    (null-over-fake). Mutates the record IN PLACE; no file I/O. Gated: skips if a summary is
    already present. Returns True if it changed the record."""
    if record.get("section_summary"):
        return False
    signal = gather_section_signal(event_data)
    src = record.get("source") or {}
    from src.utils.prompt_loader import get_system_prompt, render_prompt

    prompt = render_prompt(
        "source_section",
        section_title=signal["section_title"] or "",
        event_name=signal["event_name"] or "",
        book=src.get("book") or "",
        series=src.get("series") or "",
        sub_event_summaries="\n".join(f"- {s}" for s in signal["sub_event_summaries"])
        or "(none)",
        places=", ".join(signal["places"]) or "(none)",
        dates=", ".join(signal["dates"]) or "(none)",
    )
    result = grok_client.extract_json(
        prompt,
        system_prompt=get_system_prompt("source_section"),
        cache_type="source_section",
    )
    if not isinstance(result, dict):
        return False
    record["section_summary"] = _truncate_to_two_sentences(
        result.get("section_summary")
    )
    record["operation"] = _normalize_operation(
        result.get("operation"), confidence_threshold
    )
    return True


def _iter_event_files(output_dir: Path):
    """Yield every finalized event file (one per source section)."""
    yield from (output_dir / "content").glob("*/*-event.json")


def _find_section_record_by_event_id(output_dir: Path, event_id: str) -> Optional[Path]:
    """Locate the source_section record for an EventID (no index: scan the small dir)."""
    section_dir = output_dir / "source_section"
    if not section_dir.exists():
        return None
    import json as _json

    for p in section_dir.glob("*.json"):
        try:
            if _json.loads(p.read_text()).get("EventID") == event_id:
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


def enrich_all_source_sections(
    output_dir: Path,
    grok_client: Any,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
) -> int:
    """Separate Phase-2 pass: for each event section, find-or-create its source_section record,
    derive section_summary + operation via one LLM call, and native-commit. Returns the count
    enriched. Fail-safe per section (one failure never aborts the pass)."""
    import json as _json

    enriched = 0
    for event_file in _iter_event_files(output_dir):
        try:
            event_data = _json.loads(event_file.read_text())
        except Exception as e:  # noqa: BLE001
            logger.warning("source_section: cannot read %s: %s", event_file, e)
            continue
        event_id = (event_data.get("Event") or {}).get("EventID")
        if not event_id:
            continue
        rec_path = _find_section_record_by_event_id(output_dir, event_id)
        if rec_path is not None:
            record = _json.loads(rec_path.read_text())
        else:
            source = None
            parsed = event_file.parent / event_file.name.replace(
                "-event.json", "-parsed.json"
            )
            if parsed.exists():
                try:
                    pd = _json.loads(parsed.read_text()) or {}
                    source = {
                        "book": pd.get("book"),
                        "author": pd.get("author"),
                        "series": pd.get("series"),
                    }
                except Exception:  # noqa: BLE001
                    source = None
            record = build_source_section(event_data, source=source)
            if record is None:
                continue
        try:
            changed = derive_summary_and_operation(
                record, event_data, grok_client, confidence_threshold
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("source_section derive failed for %s: %s", event_id, e)
            continue
        if changed:
            _save_source_section(output_dir, record)
            enriched += 1
    logger.info("source_section: derived summary+operation for %d section(s)", enriched)
    return enriched
