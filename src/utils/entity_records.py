"""Reading entity records from an ``output/`` tree - the ONE definition of which files in an entity
dir are records and where a record's source mentions are. Shared by the validators
(``scripts/validate_phase2``, ``validate_phase3``, ``validate_provenance``) and the provenance
completeness audit (``src/utils/provenance_integrity``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

# Sidecars that live in entity dirs and are not schema records (indexes, reports, curated lists).
SKIP_NAMES = frozenset(
    {
        "index.json",
        ".processed_events.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "related_groups_report.json",
        "surname_frequency_suggestions.json",
        "review_queue.json",
        "not_people.json",
        "not_groups.json",
        "not_related.json",
        "not_citation.json",
        "not_equipment.json",
    }
)
# A mention's sub-event reference: entities spell it two ways.
SUBEVENT_FIELDS = ("Sub_eventID", "Sub-eventID")


def entity_files(root: Path, glob: str) -> List[Path]:
    """The record files of an entity's registry ``output_glob`` under ``root`` (the output dir)."""
    return [
        f
        for f in Path(root).glob(glob.replace("output/", "", 1))
        if not f.name.endswith(".lock")
        and f.name not in SKIP_NAMES
        and not f.name.startswith(".")
    ]


def load_json(f: Path) -> Any:
    """The file's JSON, or None when unreadable."""
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def iter_mentions(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A record's source mentions: ``event_mentions[]``; for casualties their single
    ``event_context`` (a present, real EventID - authoritative, see src/loader/load.py); for
    bibliography ``mentions[]``. ``[]`` when it has none."""
    ems = rec.get("event_mentions")
    if isinstance(ems, list) and ems:
        return ems
    ectx = rec.get("event_context")
    if isinstance(ectx, dict):
        eid = str(ectx.get("EventID", "")).strip().lower()
        if eid and eid not in ("none", "null"):
            return [ectx]
    mentions = rec.get("mentions")
    if isinstance(mentions, list) and mentions:
        return mentions
    return ems if isinstance(ems, list) else []


def mention_sub_event(m: Dict[str, Any]) -> str:
    """The sub-event a mention cites ("" when none), whichever spelling it uses."""
    for k in SUBEVENT_FIELDS:
        if m.get(k):
            return str(m[k])
    return ""


def mention_identity(m: Dict[str, Any]) -> tuple:
    """What makes two mentions of ONE entity the same mention: an entity appears in a sub-event
    once (re-extracting a chapter must not stack a second mention per sub-event), so a mention with
    a sub-event is identified by its sub-event (unique across events); one without by (EventID, its normalised
    quote) - two different quotes of an event are two mentions."""
    from src.utils.quote_match import normalize

    seid = mention_sub_event(m)
    if seid:
        return ("", seid)  # a Sub-eventID is unique across events
    return (str(m.get("EventID") or ""), "", normalize(m.get("original_text")))


def citation_identity(m: Dict[str, Any]) -> tuple:
    """What makes two bibliography mentions the same citation: the same note (event, sub-event,
    note number) - or, for a note without a number, the same quoted text (else every unnumbered
    note of a sub-event would collapse into one and lose its text)."""
    from src.utils.quote_match import normalize

    number = str(m.get("reference_number") or "").strip()
    text = "" if number else normalize(m.get("verbatim_reference"))
    return (str(m.get("EventID") or ""), mention_sub_event(m), number, text)


def merge_mentions(
    into: List[Dict[str, Any]], more: Any, identity=mention_identity
) -> int:
    """Append to ``into`` (in place) each mention of ``more`` it does not already hold, by
    ``identity``; a kept mention is copied as is (its quote, locator and links untouched). Returns
    how many were added. The ONE mention union of every entity merge (extraction re-runs, dedup
    auto-merge, the Dedup Review UI, bibliography reconcile)."""
    seen = {identity(m) for m in into if isinstance(m, dict)}
    added = 0
    for m in more or []:
        if not isinstance(m, dict):
            continue
        key = identity(m)
        if key not in seen:
            into.append(m)
            seen.add(key)
            added += 1
    return added


# Entities whose ``enrichment_status == "enriched"`` means a DESCRIPTIVE external-source
# enrichment, so a source link is expected (validate_phase3 P3-A5, validate_provenance P3-A1).
ENRICHED_ENTITIES = ("people", "people_groups", "equipment")


def is_enriched(rec: Dict[str, Any]) -> bool:
    return str(rec.get("enrichment_status", "")).strip() == "enriched"


# Fields that signal an external-source LINK; checked at top level and inside biographical_profile.
LINK_FIELDS = ("wikipedia_url", "grokipedia_url", "source_url", "archive_url")


def _dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _any_link(d: Dict[str, Any]) -> bool:
    return any(d.get(k) for k in LINK_FIELDS)


def _any_entry_with(items: Any, *keys: str) -> bool:
    """A list holding at least one dict with a non-empty value under one of ``keys``."""
    return isinstance(items, list) and any(
        isinstance(i, dict) and any(i.get(k) for k in keys) for i in items
    )


def has_source_link(rec: Dict[str, Any]) -> bool:
    """The record names where its enrichment came from. The ONE attribution predicate
    (validate_phase3 P3-A5, validate_provenance P3-A1). A source is any of:

    * a wiki/grok/archive/source URL at the top level or in ``biographical_profile``;
    * a biography's ``biography_sources[].page`` (a biography built from sources lists each);
    * equipment's ``external_data`` URL or ``additional_sources[].url`` (its schema forbids a
      top-level URL);
    * ``resource_urls``;
    * a structured enrichment's ``enrichment_data._source.type`` (e.g. groups via the Grok
      group_enrichment prompt: {type, query, retrieved_at} - traceable attribution).
    """
    bp, ext = _dict(rec.get("biographical_profile")), _dict(rec.get("external_data"))
    source = _dict(_dict(rec.get("enrichment_data")).get("_source"))
    return bool(
        _any_link(rec)
        or _any_link(bp)
        or _any_entry_with(bp.get("biography_sources"), "page", "url")
        or _any_link(ext)
        or _any_entry_with(ext.get("additional_sources"), "url")
        or (isinstance(rec.get("resource_urls"), list) and rec.get("resource_urls"))
        or source.get("type")
    )
