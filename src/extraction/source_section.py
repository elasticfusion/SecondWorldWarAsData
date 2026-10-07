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

from src.schemas import entity_version
from src.utils.file_lock import write_json_with_lock

# This module is the contract writer for the source_section entity: the version of the
# schema it targets must equal the entity's current version (enforced by the target guard).
SCHEMA_TARGET = entity_version("source_section")

ENTITY = "source_section"


def _save_source_section(output_dir: Path, record: Dict[str, Any]) -> Optional[Path]:
    """Write one source_section record via the native, version-stamped locked writer."""
    section_id = record.get("SourceSectionID")
    if not section_id:
        return None
    section_dir = output_dir / "source_section"
    section_dir.mkdir(parents=True, exist_ok=True)
    path = section_dir / f"{section_id}.json"
    write_json_with_lock(path, record, entity=ENTITY)
    return path
