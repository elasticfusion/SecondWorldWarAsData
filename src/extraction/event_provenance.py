"""Stamp a resolvable SourceSectionID onto every content event (+ its sub-events).

Provenance hardening (XChannel-8 E2E finding): the entity -> event_mentions -> EventID chain
resolved, but the content event itself carried NO back-pointer to the parsed source section —
the tie was filename-convention only (``chapter1full-event.json`` <-> ``chapter1full-parsed.json``),
not a stored ID. This closes that gap by writing ``Event.SourceSectionID`` (and the same onto each
Sub-event) so entity -> event -> SECTION is a hard, enforceable ID chain that the end-of-phase
referential-integrity validator can then resolve like any other edge.

The canonical source entity is ``out/source_section/*.json``; each record carries BOTH its
``SourceSectionID`` and the ``EventID`` of the event it was derived from. We invert that into an
``EventID -> SourceSectionID`` index and stamp it back onto the matching event. Fail-safe: a missing
index entry leaves the field null (the completeness guard reports it; it is never fatal here).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _section_event_pair(sf: Path):
    """(EventID, SourceSectionID) from a source_section file, or None. Fail-safe."""
    try:
        rec = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rec, dict):
        return None
    eid = rec.get("EventID")
    sid = rec.get("SourceSectionID")
    if isinstance(eid, str) and eid and isinstance(sid, str) and sid:
        return (eid, sid)
    return None


def _build_event_section_index(out: Path) -> Dict[str, str]:
    """Map ``EventID`` -> ``SourceSectionID`` from the committed source_section records.

    The source_section is the authoritative source entity; this is the single resolvable join from
    an event back to the section it came from. Fail-safe -> {}.
    """
    index: Dict[str, str] = {}
    try:
        sec_dir = out / "source_section"
        if not sec_dir.exists():
            return index
        for sf in sec_dir.glob("*.json"):
            if sf.name == "index.json":
                continue
            pair = _section_event_pair(sf)
            if pair:
                index.setdefault(pair[0], pair[1])
    except Exception as e:  # noqa: BLE001
        logger.debug("event->section index skipped: %s", e)
    return index


def _load_event(event_file: Path):
    """(data, event_dict) from an event file, or (None, None) if unusable. Fail-safe."""
    try:
        data = json.loads(event_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(data, dict) or not isinstance(data.get("Event"), dict):
        return None, None
    return data, data["Event"]


def _apply_sid(event: Dict[str, Any], sid: str) -> bool:
    """Set SourceSectionID on the event + each sub-event. Returns True if anything changed."""
    changed = False
    if event.get("SourceSectionID") != sid:
        event["SourceSectionID"] = sid
        changed = True
    for sub in event.get("Sub-events") or []:
        if isinstance(sub, dict) and sub.get("SourceSectionID") != sid:
            sub["SourceSectionID"] = sid
            changed = True
    return changed


def _stamp_one_event(event_file: Path, index: Dict[str, str]) -> bool:
    """Stamp SourceSectionID onto one content event + its sub-events. Returns True if changed.

    Resolves the owning SourceSectionID via the event's own EventID. Fail-safe: leaves the field
    untouched if the EventID is unknown to the index. Never raises into the caller.
    """
    data, event = _load_event(event_file)
    if event is None:
        return False
    eid = event.get("EventID")
    sid = index.get(eid) if isinstance(eid, str) else None
    if not sid:
        return False
    if _apply_sid(event, sid):
        _write_event(event_file, data)
        return True
    return False


def _write_event(event_file: Path, data: Dict[str, Any]) -> None:
    """Persist the event via the central guarded writer (schema-validated)."""
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(event_file, data, entity="events")


def stamp_event_source_sections(out: Path) -> int:
    """Stamp SourceSectionID onto every content event under ``out/content``.

    Returns the number of event files updated. Fail-safe end-to-end.
    """
    index = _build_event_section_index(Path(out))
    if not index:
        logger.info(
            "OBSERV event_provenance.stamp resolved=0 reason=no_source_sections"
        )
        return 0

    stamped = 0
    scanned = 0
    try:
        for ef in (Path(out) / "content").rglob("*-event.json"):
            if ef.name.endswith("-notes-event.json"):
                continue
            scanned += 1
            if _stamp_one_event(ef, index):
                stamped += 1
    except Exception as e:  # noqa: BLE001 - stamping must never crash the phase
        logger.debug("event_provenance stamp walk skipped: %s", e)

    logger.info(
        "OBSERV event_provenance.stamp scanned=%d stamped=%d index_size=%d",
        scanned,
        stamped,
        len(index),
    )
    return stamped
