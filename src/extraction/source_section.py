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
from datetime import date

import ulid

from src.grok_client import for_chapter_book
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


def _save_source_section(
    output_dir: Path, record: Dict[str, Any], path: Optional[Path] = None
) -> Optional[Path]:
    """Write one source_section record via the native, version-stamped locked writer.

    ``path`` writes the record back WHERE IT LIVES (an existing record found under
    ``content/<book>/source_section/``); without it the record goes to ``output_dir/source_section``.
    """
    section_id = record.get("SourceSectionID")
    if not section_id:
        return None
    if path is None:
        path = output_dir / "source_section" / f"{section_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    inject_metadata(record, entity=ENTITY)
    if not write_json_with_lock(path, record, entity=ENTITY):
        return None  # guarded write refused: do not report a save that did not happen
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


def _rollup_summaries(summaries: list) -> list:
    """Roll up an oversized sub-event summary list to a bounded head+tail (with an ellipsis
    marker) so the prompt stays bounded but representative."""
    if len(summaries) > _MAX_SUBEVENTS_INLINE:
        # Rolling reduction: keep head + tail so the prompt stays bounded but representative.
        head = summaries[: _MAX_SUBEVENTS_INLINE // 2]
        tail = summaries[-_MAX_SUBEVENTS_INLINE // 2 :]
        summaries = head + ["…"] + tail
    return summaries


def _aggregate_place_date_refs(subs: list) -> tuple:
    """Aggregate de-duplicated place/date references across sub-events, preserving order."""
    places: list = []
    dates: list = []
    for s in subs:
        for p in s.get("places") or []:
            if p not in places:
                places.append(p)
        for d in s.get("dates") or []:
            if d not in dates:
                dates.append(d)
    return places, dates


def gather_section_signal(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Collect the CUMULATIVE-SUMMARY signal for an event section: title, event name, the
    distilled sub-event summaries, and aggregated place/date references. Does NOT read the
    raw chapter (cheaper, no token-limit risk). Rolls up sub-events if the list is oversized.
    """
    event = event_data.get("Event") or {}
    subs = event.get("Sub-events") or []
    summaries = [s.get("Sub-event_summary") for s in subs if s.get("Sub-event_summary")]
    summaries = _rollup_summaries(summaries)
    places, dates = _aggregate_place_date_refs(subs)
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


def _build_section_prompt(signal: Dict[str, Any], src: Dict[str, Any]) -> str:
    """Render the source_section LLM prompt from the gathered signal + source metadata."""
    from src.utils.prompt_loader import render_prompt

    return render_prompt(
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
    from src.utils.prompt_loader import get_system_prompt

    prompt = _build_section_prompt(signal, src)
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


def fetch_section_articles(record: Dict[str, Any]) -> bool:
    """When an operation is present, fetch Grokipedia + Wikipedia articles as additive
    reference material (text + inline references). Gated on wikipedia_checked_at (stamped even
    on a miss, so it is never re-fetched). Mutates the record IN PLACE; returns True if it ran
    the fetch step (whether or not articles were found)."""
    if record.get("wikipedia_checked_at"):
        return False
    operation = record.get("operation")
    # Stamp the gate marker up front so a miss (or no-operation) is never re-attempted.
    record["wikipedia_checked_at"] = date.today().isoformat()
    if not operation:
        return True
    from src.extraction.source_section_articles import fetch_reference_articles

    articles = fetch_reference_articles(operation)
    if articles:
        existing = record.get("reference_articles") or []
        record["reference_articles"] = existing + articles
    return True


def _iter_event_files(output_dir: Path):
    """Yield every finalized event file (one per source section)."""
    yield from (output_dir / "content").glob("*/*-event.json")


def _candidate_section_files(output_dir: Path, near: Optional[Path]):
    """Record files to search: the event's own ``source_section`` dir first (where the event pass
    emitted it - the common case reads one directory), then every other location."""
    from src.utils.source_sections import section_dirs

    dirs = [d for d in ([near] if near else []) if d.is_dir()]
    dirs += [d for d in section_dirs(output_dir) if d not in dirs]
    for d in dirs:
        for p in sorted(d.glob("*.json")):
            if p.name != "index.json":
                yield p


def _record_event_id(p: Path, wanted: str = "?") -> Optional[str]:
    """EventID of a record file, or None - LOUD when unreadable (a missed record means a duplicate
    ULID is minted for the same event)."""
    import json as _json

    try:
        return _json.loads(p.read_text()).get("EventID")
    except Exception as _e:  # noqa: BLE001
        logger.warning(
            "OBSERV source_section.record_read_failed file=%s event_id=%s "
            "reason=%s (duplicate-ULID risk: existing record may be missed)",
            p.name,
            wanted,
            type(_e).__name__,
        )
        return None


def _find_section_record_by_event_id(
    output_dir: Path, event_id: str, near: Optional[Path] = None
) -> Optional[Path]:
    """Locate the source_section record for an EventID (layout: src/utils/source_sections)."""
    for p in _candidate_section_files(output_dir, near):
        if _record_event_id(p, event_id) == event_id:
            return p
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
    attempted = 0
    skipped = 0
    errors = 0
    for event_file in _iter_event_files(output_dir):
        attempted += 1
        status = _enrich_one_section(
            event_file, output_dir, grok_client, confidence_threshold
        )
        if status == "enriched":
            enriched += 1
        elif status == "skipped":
            skipped += 1
        elif status == "error":
            errors += 1
    logger.info("source_section: enriched %d section(s)", enriched)
    logger.info(
        "OBSERV source_section.enrich attempted=%d enriched=%d skipped_no_eventid=%d errors=%d",
        attempted,
        enriched,
        skipped,
        errors,
    )
    return enriched


def _load_or_build_section_record(
    event_file: Path,
    output_dir: Path,
    event_id: str,
    event_data: Dict[str, Any],
):
    """Find the existing source_section record for ``event_id``, or build a new one from the
    event (pulling book/author/series from the sibling -parsed.json when present). Returns
    ``(record, existing_path)``: ``existing_path`` is where a found record lives (save back THERE),
    None for a newly built one; ``record`` is None if none could be built."""
    import json as _json

    rec_path = _find_section_record_by_event_id(
        output_dir, event_id, near=event_file.parent / "source_section"
    )
    if rec_path is not None:
        return _json.loads(rec_path.read_text()), rec_path
    source = None
    parsed = event_file.parent / event_file.name.replace("-event.json", "-parsed.json")
    if parsed.exists():
        try:
            pd = _json.loads(parsed.read_text()) or {}
            source = {
                "book": pd.get("book"),
                "author": pd.get("author"),
                "series": pd.get("series"),
            }
        except Exception as _e:  # noqa: BLE001
            logger.warning(
                "OBSERV source_section.record_read_failed file=%s event_id=%s "
                "reason=%s (parsed sibling unreadable; building record without source)",
                parsed.name,
                event_id,
                type(_e).__name__,
            )
            source = None
    return build_source_section(event_data, source=source), None


def _fetch_section_extras(record: Dict[str, Any], output_dir: Path, event_id: str):
    """Run the two fail-safe post-derive fetch steps (reference articles + Wikipedia media).
    Returns ``(fetched, media_written)``."""
    # Fetch Grokipedia + Wikipedia reference articles (gated on wikipedia_checked_at).
    try:
        fetched = fetch_section_articles(record)
    except Exception as e:  # noqa: BLE001
        logger.warning("source_section article fetch failed for %s: %s", event_id, e)
        fetched = False
    # Fetch Wikipedia media (photos/maps) -> first-class images records cross-linked.
    try:
        from src.extraction.source_section_media import fetch_section_media

        media_written = fetch_section_media(record, output_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("source_section media fetch failed for %s: %s", event_id, e)
        media_written = 0
    return fetched, media_written


def _read_event(event_file: Path):
    """(event_data, event_id, status): status is '' when usable, else 'error' | 'skipped'."""
    import json as _json

    try:
        event_data = _json.loads(event_file.read_text())
    except Exception as e:  # noqa: BLE001
        logger.warning("source_section: cannot read %s: %s", event_file, e)
        return None, None, "error"
    event_id = (event_data.get("Event") or {}).get("EventID")
    if not event_id:
        return event_data, None, "skipped"
    return event_data, event_id, ""


def _note_record_rebuilt(event_id: str) -> None:
    """No existing record was found for this event: a NEW SourceSectionID is minted. If S3 already
    holds one for this EventID (the content tree was not rehydrated) that makes two - say so.
    """
    from src.utils.observe import observe_warn_counted

    observe_warn_counted(
        "source_section",
        "record_rebuilt",
        key=("record_rebuilt",),
        every=25,
        note=(
            "no existing source_section record was found for an event, so a new one (new ID) was "
            "created; if the data bucket already has a record for this EventID under "
            "output/content/<book>/source_section/, delete the duplicate and re-run Phase 2 "
            "with output/content/ rehydrated"
        ),
        event_id=event_id,
    )


def _commit_section(
    record: Dict[str, Any],
    rec_path: Optional[Path],
    event_file: Path,
    output_dir: Path,
    event_id: str,
    enriched: bool,
) -> str:
    """Save the record where it lives (new ones next to their event, never into an unsynced
    ``output/source_section``). The idempotency gates (media_checked_at / wikipedia_checked_at) are
    stamped even when a lookup found nothing, so they are saved too. Returns the status.
    """
    target = rec_path or (
        event_file.parent / "source_section" / f"{record['SourceSectionID']}.json"
    )
    if _save_source_section(output_dir, record, target) is not None:
        return "enriched" if enriched else "noop"
    from src.utils.observe import observe_warn

    observe_warn(
        "source_section",
        "save_refused",
        note=(
            "the enriched source_section could not be saved (schema guard or missing "
            "SourceSectionID); its Wikipedia images are already written, so a re-run may "
            "duplicate them - inspect the schema error in this task's log"
        ),
        event_id=event_id,
    )
    return "error"


def _derive_and_fetch(
    record: Dict[str, Any],
    event_data: Dict[str, Any],
    event_id: str,
    output_dir: Path,
    grok_client: Any,
    confidence_threshold: float,
) -> Optional[bool]:
    """Derive summary+operation and fetch articles + media into ``record``. True if anything was
    enriched, False if not, None if the derivation itself failed."""
    try:
        changed = derive_summary_and_operation(
            record, event_data, grok_client, confidence_threshold
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("source_section derive failed for %s: %s", event_id, e)
        return None
    fetched, media_written = _fetch_section_extras(record, output_dir, event_id)
    return bool(changed or fetched or media_written)


def _gate_stamps(record: Dict[str, Any]) -> tuple:
    return (record.get("media_checked_at"), record.get("wikipedia_checked_at"))


@for_chapter_book
def _enrich_one_section(
    event_file: Path,
    output_dir: Path,
    grok_client: Any,
    confidence_threshold: float,
) -> str:
    """Enrich one event's source_section (fail-safe): find-or-build the record, derive
    summary+operation, fetch reference articles + Wikipedia media, native-commit if anything
    changed. Returns a status: 'enriched' | 'skipped' (no EventID) | 'error' | 'noop'.
    """
    event_data, event_id, status = _read_event(event_file)
    if status or event_data is None or event_id is None:
        return status or "error"
    record, rec_path = _load_or_build_section_record(
        event_file, output_dir, event_id, event_data
    )
    if record is None:
        return "noop"
    if rec_path is None:
        _note_record_rebuilt(event_id)
    gates_before = _gate_stamps(record)
    enriched = _derive_and_fetch(
        record, event_data, event_id, output_dir, grok_client, confidence_threshold
    )
    if enriched is None:
        return "error"
    if not (enriched or _gate_stamps(record) != gates_before):
        return "noop"
    return _commit_section(record, rec_path, event_file, output_dir, event_id, enriched)
