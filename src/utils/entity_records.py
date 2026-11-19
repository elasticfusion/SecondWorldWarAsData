"""Reading entity records from an ``output/`` tree - the ONE definition of which files in an entity
dir are records and where a record's source mentions are. Shared by the validators
(``scripts/validate_phase2``, ``validate_phase3``, ``validate_provenance``) and the provenance
completeness audit (``src/utils/provenance_integrity``)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

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
        "coords.json",  # places/coords.json: the regenerable map coordinates cache
    }
)
# A mention's sub-event reference: entities spell it two ways.
SUBEVENT_FIELDS = ("Sub_eventID", "Sub-eventID")


def is_record_file(f: Path) -> bool:
    """True for an entity record file; False for an index, report, curated list, lock or run state
    file (any dotfile, e.g. ``.processed_events.json`` - pathlib's glob matches dotfiles).
    """
    name = Path(f).name
    return not (name.endswith(".lock") or name in SKIP_NAMES or name.startswith("."))


def entity_files(root: Path, glob: str) -> List[Path]:
    """The record files of an entity's registry ``output_glob`` under ``root`` (the output dir)."""
    return [
        f for f in Path(root).glob(glob.replace("output/", "", 1)) if is_record_file(f)
    ]


def load_json(f: Path) -> Any:
    """The file's JSON, or None when unreadable."""
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def read_record(f: Path) -> Any:
    """A stored record's JSON. Raises ``stop_cause.RecordCorrupt`` when the file exists but is
    not text or not JSON (the book's own data is bad: Phase 3 holds the book, owner 2026-11-18);
    an OSError (missing file, disk) is raised as is, since it is not corruption."""
    from src.utils.stop_cause import RecordCorrupt

    raw = Path(f).read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except (
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ) as e:  # too deeply nested too
        raise RecordCorrupt(f, type(e).__name__) from e


# The stored records this process found corrupt (task 1.2f PART 2, owner 2026-11-18): each step
# goes on with its other records, and Phase 3 then exits EXIT_DATA_CORRUPT so the book is held for
# the operator (a retry cannot repair a record).
_corrupt: Dict[str, None] = (
    {}
)  # path -> None, in the order found (a set that keeps order)
_corrupt_lock = threading.Lock()


def load_record(f: Path, scope: str, expect: type = dict) -> Any:
    """A stored record for a step to work on, or None to skip it. The ONE loader for records a
    step reads (DRY):

    * not a record file (an index, report, curated list, lock or dotfile state file:
      ``is_record_file``): None, nothing reported - those are not records and some are lists;
    * corrupt (not text, not JSON, or not a ``expect``): a loud ``record.corrupt`` alert naming
      the file (once per file per run, however many steps read it), remembered in
      ``corrupt_records()`` (Phase 3 holds the book at the end of the run), None - the step goes
      on with its other records;
    * gone (merged away mid-run): None, debug only; unreadable from disk (another OSError): an
      OBSERV warning, None. Neither is corruption.
    """
    from src.utils.observe import observe_error
    from src.utils.stop_cause import RecordCorrupt

    if not is_record_file(Path(f)):
        return None
    try:
        data = read_record(f)
        if not isinstance(data, expect):
            raise RecordCorrupt(f, f"not a {expect.__name__}")
    except RecordCorrupt as e:
        with _corrupt_lock:
            first = str(f) not in _corrupt
            _corrupt[str(f)] = None
        if not first:
            return None
        observe_error(
            "record",
            "corrupt",
            note=(
                "A stored record cannot be read, so it was skipped and the book is held at the "
                "end of the run. Repair or delete the file (and its S3 copy), then resume the "
                "book"
            ),
            file=Path(f).name,
            entity=Path(f).parent.name,
            step=scope,
            reason=str(e).rsplit(": ", 1)[-1][:60],
        )
        return None
    except FileNotFoundError:
        # merged away or relocated mid-run (dedup): expected, the next run sees the corpus as is
        logger.debug("record %s gone before %s read it", Path(f).name, scope)
        return None
    except OSError as e:
        logger.warning(
            "OBSERV record.unreadable step=%s file=%s err=%s status=warn - skipped this run; "
            "check the disk / S3 sync if this repeats",
            scope,
            Path(f).name,
            type(e).__name__,
        )
        return None
    return data


def corrupt_records() -> List[str]:
    """The corrupt stored records found by ``load_record`` in this process (each once, in
    order)."""
    with _corrupt_lock:
        return list(_corrupt)


def reset_corrupt_records() -> None:
    with _corrupt_lock:
        _corrupt.clear()


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
    """What makes two mentions of ONE entity the same mention: the same statement in the same
    place of the source - its sub-event (unique across events; else its EventID) and its
    normalised quote. Every distinct statement is its own mention: a sub-event that names a
    person twice, or reports the weather at Omaha twice, keeps both - as does another sub-event,
    chapter or book. A mention without a quote is told apart by what it says (every field but its
    minted ids): the same statement re-applied is one mention, two different ones are two -
    dropping a real statement is worse than keeping a possible duplicate. A re-extracted chapter
    mints new Sub-eventIDs and new wording - purge it first (scripts/purge_chapter_event.py).
    """
    from src.utils.quote_match import normalize

    seid = mention_sub_event(m)
    where = ("", seid) if seid else (str(m.get("EventID") or ""), "")
    quote = normalize(m.get("original_text"))
    if not quote:
        return where + ("", _statement_fingerprint(m))
    return where + (quote, "")


# A mention's own minted ids: a re-applied answer gets new ones, so they never tell two statements
# apart (a link to ANOTHER record, e.g. a person mention's DateMentionID, is content - but dates
# mint their own mention id under that name, so it is left out too).
_MINTED_IDS = frozenset(("MentionID", "EventMentionID", "DateMentionID"))


def _statement_fingerprint(m: Dict[str, Any]) -> str:
    """What a quote-less mention says - every field but its minted ids - so the same statement
    re-applied (a resumed or retried chapter) is one mention, and two different ones are two.
    """
    import json

    return json.dumps(
        {k: v for k, v in m.items() if k not in _MINTED_IDS},
        sort_keys=True,
        default=str,
    )


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


def set_alt_title(record: Dict[str, Any], value: Any) -> None:
    """A bibliography record's alt_title, in BOTH places it lives (top level and citation): the
    write guard rejects a record whose two copies differ (file_lock._bibliography_alt_title_ok),
    so setting only one lost the whole write (2026-11-14: reconcile merges of 4 citations).
    """
    citation = record.get("citation")
    if not isinstance(citation, dict):
        citation = record["citation"] = {}
    record["alt_title"] = value
    citation["alt_title"] = value


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
