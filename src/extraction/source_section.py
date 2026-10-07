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

import ulid

from src.schemas import entity_version, inject_metadata
from src.utils.file_lock import write_json_with_lock

# This module is the contract writer for the source_section entity: the version of the
# schema it targets must equal the entity's current version (enforced by the target guard).
SCHEMA_TARGET = entity_version("source_section")

ENTITY = "source_section"


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
