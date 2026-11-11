"""Event and sub-event extraction from parsed content."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import ValidationError, validate

from src.grok_client import GrokClient, GrokAPIError, current_book
from src.json_schemas import EVENT_SCHEMA
from src.utils.json_validator import _fix_invalid_ulids
from src.schemas import ULID_PATTERN

from src.utils.prompt_loader import get_system_prompt

logger = logging.getLogger(__name__)


def _is_footnote_paragraph(para: Dict[str, Any], all_paragraphs: list) -> bool:
    """Check if a paragraph is part of the footnote section."""
    text = para["text"].strip()

    # Check if this is the footnote section header
    if text.lower() in ["### footnotes", "## footnotes", "# footnotes", "footnotes"]:
        return True

    # Find if there's a footnote header before this paragraph
    para_idx = None
    footnote_header_idx = None

    for i, p in enumerate(all_paragraphs):
        if p == para:
            para_idx = i
        p_text = p["text"].strip().lower()
        if p_text in ["### footnotes", "## footnotes", "# footnotes", "footnotes"]:
            footnote_header_idx = i

    # If we found both and this para is after the footnote header, it's a footnote
    if (
        para_idx is not None
        and footnote_header_idx is not None
        and para_idx > footnote_header_idx
    ):
        return True

    return False


def validate_event_json(data: Dict[str, Any]) -> None:
    """
    Validate event JSON against schema and check ULID uniqueness.

    Raises:
        ValidationError: If JSON doesn't match schema or has duplicate IDs
    """
    validate(instance=data, schema=EVENT_SCHEMA)

    # Enforce unique Sub-eventIDs within this file
    sub_events = data.get("Event", {}).get("Sub-events", [])
    seen_ids = set()
    for se in sub_events:
        seid = se.get("Sub-eventID")
        if seid and seid in seen_ids:
            raise ValidationError(f"Duplicate Sub-eventID within file: {seid}")
        if seid:
            seen_ids.add(seid)


def _build_subevent_fulltext(se: dict, para_lookup: dict) -> None:
    """Replace one sub-event's 'paragraphs' number array with a 'Sub-event_fulltext' dict,
    looking up paragraph text from ``para_lookup``. Falls back to the summary when the fulltext
    would otherwise be empty. Mutates ``se`` in place; no-op if fulltext is already present.
    """
    # Grok emits `paragraph_numbers` (see prompts/events.yaml); older cached responses used
    # `paragraphs`. Read the correct key first — a mismatch here silently empties the fulltext and
    # forces the summary fallback below, starving every downstream entity pass of source text.
    nums = se.pop("paragraph_numbers", None)
    if nums is None:
        nums = se.pop("paragraphs", None)
    if nums is None and "Sub-event_fulltext" in se:
        return  # Already has fulltext (cached old-format response)
    from src.utils.paragraph_numbers import coerce_paragraph_numbers

    fulltext = {}
    for n in coerce_paragraph_numbers(nums, scope="events.fulltext"):
        if n in para_lookup:
            fulltext[f"Paragraph_{n}"] = para_lookup[n]
        else:
            logger.warning("  Paragraph %s not found in parsed data, skipping", n)
    se["Sub-event_fulltext"] = fulltext

    # Fallback: if fulltext is empty but summary exists, use summary as text. This is a DEGRADED
    # path — entity extraction then sees only the summary, not source paragraphs — so log it LOUDLY
    # (WARNING + OBSERV) rather than silently at debug (observability gap, XChannel-8 2026-10-28).
    if not fulltext and se.get("Sub-event_summary"):
        logger.warning(
            "OBSERV events.fulltext_fallback sub_event=%s reason=no_resolved_paragraphs "
            "nums=%s — entity extraction will see SUMMARY ONLY (degraded yield)",
            se.get("Sub-eventID"),
            nums,
        )
        se["Sub-event_fulltext"] = {"summary": se["Sub-event_summary"]}


def _known_paragraphs(parsed_data: Optional[dict]) -> Optional[set]:
    """The paragraph numbers actually given to the model, or None when unknown."""
    nums = {
        p["absolute_number"]
        for p in (parsed_data or {}).get("paragraphs", []) or []
        if isinstance(p, dict) and isinstance(p.get("absolute_number"), int)
    }
    return nums or None


def _paragraph_claims(subs: list, known: Optional[set] = None) -> list:
    """[(index, [paragraph numbers in the model's order])] for each sub-event that cites any
    (the model's ``paragraph_numbers``, or the older ``paragraphs`` key). With ``known``, a cited
    number that is not one of the given paragraphs takes no part in ownership (it resolves to no
    text anyway, and must not make a claim look bigger or steal a real paragraph)."""
    from src.utils.paragraph_numbers import coerce_paragraph_numbers

    claims = []
    for i, se in enumerate(subs):
        if not isinstance(se, dict):
            continue
        raw = se.get("paragraph_numbers")
        if raw is None:
            raw = se.get("paragraphs")
        if raw is None:
            continue  # cached old-format sub-event that already carries its fulltext
        nums = list(
            dict.fromkeys(coerce_paragraph_numbers(raw, scope="events.disjoint"))
        )
        if known is not None:
            nums = [n for n in nums if n in known]
        claims.append((i, nums))
    return claims


def _make_paragraphs_disjoint(
    response: dict, parsed_data: Optional[dict] = None
) -> int:
    """Give every paragraph to exactly ONE sub-event. Returns the number of contested paragraphs.

    XChannel-8 (2026-11-07): the model assigned Paragraph_39-262 to "U.S. airborne assault" and
    Paragraph_124-262 to "Seaborne beach landings", so 105 paragraphs were extracted twice for
    every entity type (people, places, units, dates, equipment, weather), attributed to both
    sub-events, and their footnotes counted twice. A contested paragraph goes to the claimant with
    the SMALLEST claim (the more specific grouping; the earlier sub-event on a tie): the airborne
    assault keeps 39-123, the beach landings 124-262. Runs BEFORE the footnote refs and the
    fulltext are built from the paragraph arrays. A sub-event left with no paragraph keeps its
    summary (the existing summary fallback) and is reported. Note markers that occur ONLY in the
    paragraphs a sub-event gave up are removed from its ``references`` too (they belong to the
    sub-event that now owns those paragraphs).
    """
    subs = (response.get("Event", {}) or {}).get("Sub-events", []) or []
    claims = _paragraph_claims(subs, _known_paragraphs(parsed_data))
    owner, contested = _paragraph_owners(claims)
    text = _paragraph_text_lookup(parsed_data)
    emptied = []
    for i, nums in claims:
        kept = _keep_owned(subs[i], nums, owner, i, text)
        if nums and not kept:
            emptied.append(str(subs[i].get("Sub-event_summary") or "")[:60])
    if contested:
        logger.info(
            "OBSERV events.paragraph_overlap_resolved contested=%d sub_events=%d emptied=%d "
            "(each paragraph now belongs to one sub-event; emptied ones keep their summary)%s",
            len(contested),
            len(claims),
            len(emptied),
            f" emptied_summaries={emptied}" if emptied else "",
        )
    return len(contested)


def _paragraph_owners(claims: list) -> tuple:
    """({paragraph: owning sub-event index}, contested paragraphs): the smallest claim wins, the
    earlier sub-event on a tie."""
    size = {i: len(nums) for i, nums in claims}
    owner: dict = {}
    contested = set()
    for i, nums in claims:
        for n in nums:
            current = owner.setdefault(n, i)
            if current == i:
                continue
            contested.add(n)
            if size[i] < size[current]:
                owner[n] = i
    return owner, contested


def _keep_owned(se: dict, nums: list, owner: dict, i: int, text: dict) -> list:
    """Rewrite the sub-event's paragraph_numbers to the paragraphs it owns. Returns them."""
    kept = [n for n in nums if owner[n] == i]
    lost = [n for n in nums if owner[n] != i]
    se.pop("paragraphs", None)
    se["paragraph_numbers"] = kept
    if lost:
        _drop_lost_references(se, kept, lost, text)
    return kept


def _paragraph_text_lookup(parsed_data: Optional[dict]) -> dict:
    return {
        p["absolute_number"]: str(p.get("text", ""))
        for p in (parsed_data or {}).get("paragraphs", []) or []
        if isinstance(p, dict) and "absolute_number" in p
    }


_SUP_MARK = re.compile(r"<sup>\[(\d{1,6})\]")


def _drop_lost_references(se: dict, kept: list, lost: list, text: dict) -> None:
    """Remove from ``se["references"]`` the note markers found only in its ``lost`` paragraphs."""
    refs = se.get("references")
    if not isinstance(refs, list) or not refs or not text:
        return

    def marks(nums):
        return {int(m) for n in nums for m in _SUP_MARK.findall(text.get(n, ""))}

    elsewhere = marks(lost) - marks(kept)
    if elsewhere:
        se["references"] = [r for r in refs if _ref_number(r) not in elsewhere]


def _ref_number(ref) -> Optional[int]:
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str) and ref.isascii() and ref.isdigit():
        return int(ref)
    return None


def _reconstruct_fulltext(response: dict, parsed_data: dict) -> dict:
    """Replace 'paragraphs' number arrays with 'Sub-event_fulltext' dicts.

    Looks up paragraph text from parsed_data by absolute_number.
    """
    para_lookup = {
        p["absolute_number"]: p["text"] for p in parsed_data.get("paragraphs", [])
    }
    for se in response.get("Event", {}).get("Sub-events", []):
        _build_subevent_fulltext(se, para_lookup)
    return response


def _int_number_set(items) -> set:
    """Collect the integer ``number`` fields from a list of parsed note dicts."""
    return {
        int(x.get("number"))
        for x in (items or [])
        if isinstance(x, dict) and isinstance(x.get("number"), int)
    }


def _note_numbers_by_kind(parsed_data: dict) -> tuple:
    """``(footnote_numbers, endnote_numbers)``: Phase 1 records each note's source ``kind`` in the
    one ``footnotes`` array; a legacy separate ``endnotes`` array is still honoured."""
    notes = [n for n in (parsed_data.get("footnotes") or []) if isinstance(n, dict)]
    endnotes = [n for n in notes if n.get("kind") == "endnote"]
    footnotes = [n for n in notes if n.get("kind") != "endnote"]
    return _int_number_set(footnotes), (
        _int_number_set(endnotes) | _int_number_set(parsed_data.get("endnotes"))
    )


def _parse_note_context(parsed_data: dict) -> tuple:
    """Return ``(para_lookup, fn_nums, en_nums)`` from the parsed chapter data: a map of
    absolute paragraph number -> raw text, and the sets of known footnote/endnote numbers.
    Fail-safe: on malformed arrays returns empty structures (no classification context).
    """
    try:
        para_lookup = {
            p["absolute_number"]: p.get("text", "")
            for p in parsed_data.get("paragraphs", [])
            if isinstance(p, dict) and "absolute_number" in p
        }
        fn_nums, en_nums = _note_numbers_by_kind(parsed_data)
        return para_lookup, fn_nums, en_nums
    except (
        Exception
    ) as e:  # noqa: BLE001 - bad parsed arrays -> no classification context
        # WARNING (not debug): a PRESENT-but-unparseable paragraphs array is the corrupt-structure
        # signature of the paragraph_numbers incident (every note-ref then resolves to 0, silently).
        # Distinguish "no notes" from "notes structure broken".
        paragraphs_present = bool(
            isinstance(parsed_data, dict) and parsed_data.get("paragraphs")
        )
        if paragraphs_present:
            logger.warning(
                "OBSERV events.note_context_unparseable paragraphs present but could not be "
                "parsed (-> 0 note-refs resolved, silent bibliography loss): %s",
                e,
            )
        else:
            logger.debug("footnote-ref parse of parsed_data skipped: %s", e)
        return {}, set(), set()


def _marks_from_refs(refs) -> set:
    """Integer marker numbers from the LLM's per-sub-event ``references`` list."""
    if not isinstance(refs, list):
        return set()
    return {
        int(r)
        for r in refs
        if isinstance(r, int) or (isinstance(r, str) and r.isdigit())
    }


def _subevent_marks(se: dict, para_lookup: dict) -> set:
    """Derive a sub-event's note-marker numbers. PRIMARY: the LLM's per-sub-event
    ``references`` list. FALLBACK: scan the RAW parsed paragraphs assigned via
    ``paragraph_numbers`` (or the summarized fulltext) for ``<sup>[N]</sup>`` markers.
    """
    import re as _re

    marks = _marks_from_refs(se.get("references"))
    if marks:
        return marks
    nums = se.get("paragraph_numbers") or se.get("paragraphs")
    if isinstance(nums, list) and nums:
        text = " ".join(str(para_lookup.get(n, "")) for n in nums if isinstance(n, int))
    else:
        text = " ".join(str(v) for v in (se.get("Sub-event_fulltext") or {}).values())
    # <sup>[N](...)</sup>; bound N to 1-6 digits (guards int-digit-limit + blowup).
    return {int(m) for m in _re.findall(r"<sup>\[(\d{1,6})\]", text)}


def _classify_marks(marks: set, fn_nums: set, en_nums: set) -> tuple:
    """Split marker numbers into ``(footnotes, endnotes)``: known endnote numbers go to
    endnotes; everything else (incl. the common no-endnotes-array footnote case) to
    footnotes."""
    fnotes = sorted(n for n in marks if n not in en_nums or n in fn_nums)
    enotes = sorted(n for n in marks if n in en_nums and n not in fn_nums)
    return fnotes, enotes


def _sample_norefs(se: dict) -> None:
    """One-time OBSERV sample explaining why a sub-event yielded no marks."""
    if not getattr(_populate_footnote_refs, "_sampled", False):
        _populate_footnote_refs._sampled = True  # type: ignore[attr-defined]
        logger.info(
            "OBSERV events.refs.sample case=no_refs keys=%s references=%r paragraph_numbers=%r",
            sorted(se.keys()),
            se.get("references"),
            se.get("paragraph_numbers"),
        )


def _populate_footnote_refs(response: dict, parsed_data: dict) -> dict:
    """Populate each sub-event's ``Footnote_References`` / ``Endnote_References`` from the
    ``<sup>[N]</sup>`` markers in the RAW parsed paragraphs assigned to it (Blocker C fix).

    Phase-2 supplemental extraction (`create_supplemental_prompt`) needs these per-sub-event
    note references to attach the endnote text to the Grok prompt; without them Grok gets no
    footnotes and classifies 0 document_references → 0 bibliography. The markers live in the
    RAW parsed paragraph text (``parsed_data["paragraphs"][].text``), NOT in
    ``Sub-event_fulltext`` (which is LLM-summarized prose with the markers stripped — proven
    via OBSERV: has_sup=False). So we map each sub-event via its ``paragraphs`` number array
    to the raw text and scan THAT. MUST run BEFORE ``_reconstruct_fulltext`` pops
    ``paragraphs``. The parsed ``footnotes[]``/``endnotes`` arrays classify footnote vs
    endnote. Additive, idempotent, fail-safe (never raises).
    """
    para_lookup, fn_nums, en_nums = _parse_note_context(parsed_data)

    for se in response.get("Event", {}).get("Sub-events", []):
        # Per-sub-event isolation: one malformed sub-event must not drop refs for the rest.
        try:
            if not isinstance(se, dict):
                continue
            marks = _subevent_marks(se, para_lookup)
            if not marks:
                _sample_norefs(se)
                continue
            fnotes, enotes = _classify_marks(marks, fn_nums, en_nums)
            if fnotes:
                se["Footnote_References"] = fnotes
            if enotes:
                se["Endnote_References"] = enotes
        except Exception as e:  # noqa: BLE001 - additive; never block extraction
            logger.debug("footnote-ref population skipped for a sub-event: %s", e)
    return response


def _subevent_para_range(se: dict) -> tuple:
    """Recover a sub-event's (start, end) paragraph numbers from its reconstructed fulltext keys
    (``Paragraph_N``) so a deterministic Sub-eventID can key on an objective paragraph range.
    """
    nums = []
    for k in se.get("Sub-event_fulltext") or {}:
        if isinstance(k, str) and k.startswith("Paragraph_"):
            try:
                nums.append(int(k.split("_", 1)[1]))
            except ValueError:
                continue
    if not nums:
        return (None, None)
    return (min(nums), max(nums))


def _assign_subevent_ids(event: dict, eid: str) -> None:
    """Derive DETERMINISTIC Sub-eventIDs for each sub-event of ``event`` from the parent EventID
    + the sub-event's paragraph range and name (central derivation in
    ``src.utils.deterministic_ids``). Mutates each sub-event in place. Preserves the ordinal
    fall-back for a sub-event with neither a paragraph range nor a name (so sub_event_id never
    raises before schema validation)."""
    from src.utils.deterministic_ids import sub_event_id as _sub_event_id

    seen: set = set()
    for index, se in enumerate(event.get("Sub-events", []) or []):
        if not isinstance(se, dict):
            continue
        start, end = _subevent_para_range(se)
        name = se.get("Sub-event_Name") or se.get("Sub-event_summary")
        if start is None and end is None and not name:
            # Fail-safe: a sub-event with no paragraph range AND no name would make sub_event_id
            # raise before schema validation (aborting the whole extraction + bypassing the retry
            # loop). Fall back to the sub-event's ordinal — still deterministic within the parent.
            name = f"index:{index}"
        seid = _sub_event_id(eid, para_start=start, para_end=end, name=name)
        if seid in seen:
            # Two range-less sub-events with the same name (e.g. both left with only their
            # summary after paragraph de-overlap): keep IDs unique, still deterministic.
            seid = _sub_event_id(eid, name=f"{name}|index:{index}")
        seen.add(seid)
        se["Sub-eventID"] = seid


def _apply_deterministic_ids(response: dict, parsed_data: dict) -> dict:
    """Replace the LLM-supplied EventID/Sub-eventIDs with DETERMINISTIC, content-derived ids so a
    re-extraction of the same source unit yields identical ids (no cross-reference ripple), and the
    model's id is derived/validated rather than trusted. Central: ids come from
    ``src.utils.deterministic_ids``. Called after fulltext reconstruction, before validation.
    """
    from src.utils.deterministic_ids import event_id as _event_id

    event = response.get("Event")
    if not isinstance(event, dict):
        return response
    # Real parsed-data key is "section_id" (chunk-split files set "<base>-<chunk>"); the legacy
    # "section" key does not exist. Using the wrong key collapses every section of a chapter onto
    # one EventID (over-merge), so read section_id explicitly.
    section = parsed_data.get("section_id") or parsed_data.get("section") or "full"
    eid = _event_id(
        parsed_data.get("book"),
        parsed_data.get("chapter_title"),
        section,
    )
    event["EventID"] = eid
    _assign_subevent_ids(event, eid)
    return response


def create_event_prompt(parsed_data: Dict[str, Any]) -> Optional[str]:
    """Create prompt for event extraction."""
    book = parsed_data["book"]
    chapter = parsed_data["chapter_title"]
    author = parsed_data["author"]

    # Filter out footnote paragraphs
    all_paragraphs = parsed_data["paragraphs"]
    content_paragraphs = [
        p for p in all_paragraphs if not _is_footnote_paragraph(p, all_paragraphs)
    ]

    # Skip files that contain only footnotes
    if not content_paragraphs:
        logger.info("  Skipping: file contains only footnotes")
        return None

    # Build paragraph text from content only
    paragraphs_text = []
    for para in content_paragraphs:
        num = para["absolute_number"]
        text = para["text"]
        paragraphs_text.append(f"Paragraph_{num}: {text}")

    paragraphs = "\n\n".join(paragraphs_text)

    # Build images list
    images = []
    for img in parsed_data.get("images", []):
        url = img.get("url", "")
        alt = img.get("alt_text", "")
        if url:
            images.append(f"- {alt}: {url}")

    images_text = "\n".join(images) if images else "None"

    # Build maps list
    maps = []
    for m in parsed_data.get("maps", []):
        maps.append(f"- Map {m['map_id']}: {m['url']}")

    maps_text = "\n".join(maps) if maps else "None"

    from src.utils.prompt_loader import get_system_prompt, render_prompt

    prompt = render_prompt(
        "events",
        book=book,
        author=author,
        chapter=chapter,
        paragraphs=paragraphs,
        images_text=images_text,
        maps_text=maps_text,
    )

    return prompt


def _write_processing_summary(parsed_data: dict) -> None:
    """Write processing summary to log."""
    summary_log = Path("logs") / "processing_summary.json"
    summary_log.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "book": parsed_data["book"],
        "chapter": parsed_data["chapter_title"],
        "section": parsed_data.get("section", "full"),
        "paragraph_range": {
            "start": parsed_data["paragraphs"][0]["absolute_number"],
            "end": parsed_data["paragraphs"][-1]["absolute_number"],
            "count": len(parsed_data["paragraphs"]),
        },
    }

    with open(summary_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")


def _save_event_output(response: dict, parsed_file: Path, output_dir: Path) -> Path:
    """Save event extraction output to file."""
    output_file = output_dir / parsed_file.name.replace("-parsed.json", "-event.json")
    from src.utils.file_lock import write_json_with_lock

    # Only emit the dependent source_section if the EVENT actually persisted. The guard can BLOCK
    # a schema-invalid event; emitting the source_section anyway would create a source_section that
    # references an EventID with no event record — a dangling cross-reference born at write time.
    if write_json_with_lock(output_file, response, entity="events"):
        event_obj = response.get("Event", {}) or {}
        n_subs = len(event_obj.get("Sub-events", []) or [])
        logger.info(
            "OBSERV events.extract event_id=%s sub_events=%d persisted=1",
            event_obj.get("EventID") or "?",
            n_subs,
        )
        _observe_coverage(response, parsed_file)
        _emit_source_section_safe(response, parsed_file, output_dir)
    else:
        logger.warning(
            "Event write BLOCKED for %s — skipping its source_section to avoid a dangling ref",
            output_file.name,
        )
        logger.info(
            "OBSERV events.extract event_id=%s status=blocked persisted=0",
            (response.get("Event", {}) or {}).get("EventID") or "?",
        )
    return output_file


# Below this share of the chapter's narrative text (by characters, so headings weigh little) in
# its sub-events, the chapter's entities are extracted from part of it only: alert.
_MIN_COVERAGE = 0.8


def _observe_coverage(response: dict, parsed_file: Path) -> None:
    """How much of the chapter's narrative the sub-events hold. Every entity extractor reads the
    sub-events' fulltext, so a paragraph in no sub-event yields no people, places, units ... :
    a low share is an actionable error (re-run the chapter's event extraction). Observability
    only - never raises."""
    try:
        found = _coverage(response, parsed_file)
    except Exception as e:  # noqa: BLE001 - observability must never break extraction
        logger.warning(
            "OBSERV events.coverage status=unknown file=%s err=%s", parsed_file.name, e
        )
        return
    if found is None:
        return
    share, paragraphs, missing = found
    logger.info(
        "OBSERV events.coverage file=%s share=%.3f paragraphs=%d uncovered=%d",
        parsed_file.name,
        share,
        paragraphs,
        len(missing),
    )
    if share < _MIN_COVERAGE:
        from src.utils.observe import observe_error

        observe_error(
            "events",
            "coverage_low",
            note=(
                f"sub-events hold only {share:.0%} of the chapter's narrative: entities of the "
                "uncovered paragraphs are NOT extracted. Clear the chapter's events cache and "
                "re-run its Phase 2 event extraction"
            ),
            file=parsed_file.name,
            share=round(share, 3),
            uncovered=_ranges(missing)[:20],
        )


def _coverage(response: dict, parsed_file: Path):
    """(share of narrative characters covered, narrative paragraphs, uncovered numbers), or None
    for a chapter with no narrative."""
    with open(parsed_file, "r", encoding="utf-8") as f:
        paragraphs = json.load(f).get("paragraphs", []) or []
    return _coverage_of(response, paragraphs)


def _coverage_of(response: dict, paragraphs: list):
    """``_coverage`` over the chapter's ``paragraphs``."""
    content = _narrative_sizes(paragraphs)
    total = sum(content.values())
    if not total:
        return None
    covered = _covered_paragraphs(response)
    share = sum(n for k, n in content.items() if k in covered) / total
    return share, len(content), sorted(k for k in content if k not in covered)


def _narrative_sizes(paragraphs: list) -> Dict[int, int]:
    """{paragraph number: characters} of the narrative: no footnotes, no tables."""
    return {
        int(p["absolute_number"]): len(str(p.get("text", "")))
        for p in paragraphs
        if _is_numbered(p)
        and not _is_footnote_paragraph(p, paragraphs)
        and not _is_table(p)
    }


def _is_table(p: dict) -> bool:
    """A table paragraph holds figures, not narrative the extractors are expected to cover."""
    return str(p.get("text", "")).lstrip().startswith(("<table", "|"))


def _is_numbered(p: Any) -> bool:
    return isinstance(p, dict) and str(p.get("absolute_number", "")).isdigit()


def _covered_paragraphs(response: dict) -> set:
    covered = set()
    for se in (response.get("Event", {}) or {}).get("Sub-events", []) or []:
        fulltext = se.get("Sub-event_fulltext") if isinstance(se, dict) else None
        for key in fulltext or {}:
            key = str(key)
            if key.startswith("Paragraph_") and key[10:].isdigit():
                covered.add(int(key[10:]))
    return covered


def _ranges(numbers: List[int]) -> List[str]:
    """[3, 4, 5, 9] -> ["3-5", "9"]."""
    out: List[str] = []
    for n in numbers:
        if out and int(out[-1].split("-")[-1]) == n - 1:
            out[-1] = f"{out[-1].split('-')[0]}-{n}"
        else:
            out.append(str(n))
    return out


def _emit_source_section_safe(
    response: dict, parsed_file: Path, output_dir: Path
) -> None:
    """Emit the coarse-grained source_section anchor for this section (fail-safe: a failure
    here must never abort event extraction). One source_section per section, linked to the
    Event by EventID; summary + operation label are added by the Phase-2 LLM pass."""
    try:
        from src.extraction.source_section import emit_source_section

        source = None
        try:
            if parsed_file.exists():
                with open(parsed_file, "r", encoding="utf-8") as pf:
                    pd = json.load(pf) or {}
                source = {
                    "book": pd.get("book"),
                    "author": pd.get("author"),
                    "series": pd.get("series"),
                }
        except Exception as meta_err:  # noqa: BLE001
            # Raised DEBUG->WARNING: missing source metadata means the source_section anchor is
            # written WITHOUT book/author/series provenance -> it reads as a benign anchor while
            # silently dropping provenance. Keep the fail-safe (source stays None); make it loud.
            logger.warning(
                "OBSERV source_section.source_meta_unavailable err=%s", meta_err
            )
        emit_source_section(response, output_dir, source=source)
    except Exception as e:  # noqa: BLE001
        logger.warning("source_section emit skipped: %s", e)


def _split_paragraphs(paragraphs: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Split paragraphs at section boundaries (headings), falling back to midpoint."""
    if len(paragraphs) <= 10:
        return [paragraphs]

    # Find heading-like paragraphs (short, title-case, no period at end)
    boundaries = []
    for i, para in enumerate(paragraphs):
        text = para["text"].strip()
        if (
            i > 0
            and len(text) < 120
            and not text.endswith(".")
            and (text[0].isupper() if text else False)
            and text == text.title()
        ):
            boundaries.append(i)

    # Use best boundary near midpoint, or fall back to midpoint
    mid = len(paragraphs) // 2
    if boundaries:
        split_at = min(boundaries, key=lambda b: abs(b - mid))
    else:
        split_at = mid

    return [paragraphs[:split_at], paragraphs[split_at:]]


def _merge_event_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple event extraction results into one, combining sub-events.

    All chunks of one section share the same (deterministic) EventID by design. Sub-eventIDs are
    normally disjoint (keyed on paragraph range), but the positional fallback (``index:N``) resets
    per chunk, so two chunks could emit the same Sub-eventID. Deterministically disambiguate any
    collision here so the merged file keeps unique Sub-eventIDs (validate_event_json enforces
    per-file uniqueness, which this path would otherwise bypass)."""
    base = results[0]
    for extra in results[1:]:
        extra_subs = extra.get("Event", {}).get("Sub-events", [])
        base.setdefault("Event", {}).setdefault("Sub-events", []).extend(extra_subs)
    seen: set = set()
    subs = base.get("Event", {}).get("Sub-events", []) or []
    for pos, se in enumerate(subs):
        if not isinstance(se, dict):
            continue
        seid = se.get("Sub-eventID")
        if seid in seen:
            from src.utils.deterministic_ids import sub_event_id as _sub_event_id

            se["Sub-eventID"] = _sub_event_id(
                base.get("Event", {}).get("EventID", ""), name=f"merged:{pos}"
            )
        seen.add(se.get("Sub-eventID"))
    return base


def _book_of(parsed_data: Dict[str, Any]) -> Optional[str]:
    """The book a chapter's requests are asked for: the chapter's folder slug (``current_book``,
    set per chapter), else the parsed ``book`` (Phase 1 enforces the two are equal). Passed to the
    client explicitly so a worker thread that lost the context still asks for the right book.
    """
    return current_book.get() or parsed_data.get("book") or None


def _extract_chunk(
    parsed_data: Dict[str, Any],
    paragraphs: List[Dict[str, Any]],
    chunk_idx: int,
    grok_client: GrokClient,
) -> Optional[Dict[str, Any]]:
    """Extract events from a paragraph chunk."""
    chunk_data = {**parsed_data, "paragraphs": paragraphs}
    prompt = create_event_prompt(chunk_data)
    if prompt is None:
        return None

    logger.info(
        "  Extracting chunk %d (%d paragraphs)...", chunk_idx + 1, len(paragraphs)
    )
    result = grok_client.extract_json(
        prompt=prompt,
        system_prompt=get_system_prompt("events"),
        temperature=0.1,
        use_cache=True,
        cache_type="events",
        book=_book_of(parsed_data),
    )
    if "EventID" in result and "Event" not in result:
        result = {"Event": result}
    # Blocker C (both paths): populate footnote refs from the chunk's raw paragraphs
    # BEFORE _reconstruct_fulltext pops them. Without this, auto-split chapters yield
    # sub-events with no Footnote_References -> 0 bibliography for split documents.
    _make_paragraphs_disjoint(result, chunk_data)
    _populate_footnote_refs(result, chunk_data)
    _reconstruct_fulltext(result, chunk_data)
    _apply_deterministic_ids(result, chunk_data)
    validate_event_json(result)
    return result


def _try_fix_ulid_errors(
    response: dict, e: ValidationError, parsed_file: Path, output_dir: Path
) -> Optional[Path]:
    """Try to fix ULID validation errors automatically."""
    if f"does not match '{ULID_PATTERN}'" not in e.message:
        return None

    try:
        fixed_response = _fix_invalid_ulids(response)
        if isinstance(fixed_response, dict):
            validate_event_json(fixed_response)
            found = _coverage(fixed_response, parsed_file)
            if found is not None and found[0] < _MIN_COVERAGE:
                return None  # fixed ids, but too little of the chapter: the retry loop decides
            logger.info("Fixed invalid ULIDs automatically")
            return _save_event_output(fixed_response, parsed_file, output_dir)
    except Exception as fix_err:  # noqa: BLE001
        # Do NOT shadow the ValidationError `e` behind a generic message: the ULID auto-fix
        # itself failed, and the ORIGINAL validation cause is the actionable signal. Log both
        # (the real fix failure AND the original e.message) so the drop is diagnosable, not masked.
        logger.warning(
            "OBSERV events.ulid_autofix_failed fix_err=%s original_cause=%s",
            fix_err,
            e.message,
        )

    return None


def _build_retry_prompt(prompt: str, error_msg: str, ulid_error: bool) -> str:
    """Build prompt with validation feedback for retry."""
    ulid_warning = (
        "\n\nYou are not correctly creating ULIDs. Please correct and review the included specification: https://github.com/ulid/spec\nRemember: NO SPACES, exactly 26 characters, only use 0-9 A-H J-K M-N P-T V-Z"
        if ulid_error
        else ""
    )

    return f"""{prompt}

PREVIOUS ATTEMPT FAILED VALIDATION:
{error_msg}{ulid_warning}

Please fix the JSON to match the schema exactly. Ensure:
- All required fields are present
- "paragraphs" is an array of integer paragraph numbers only
- Images and Maps are arrays of [url, description] pairs
- References are arrays of integers
"""


def _auto_split_and_extract(
    parsed_data: Dict[str, Any],
    parsed_file: Path,
    output_dir: Path,
    grok_client: GrokClient,
    require_coverage: bool = False,
) -> Optional[Path]:
    """Split a too-large chapter into chunks, extract each, merge results. With
    ``require_coverage`` the merged sub-events must hold the chapter's narrative (_MIN_COVERAGE):
    otherwise nothing is saved, the chunks' cached answers are evicted, and None is returned.
    """
    content_paras = [
        p
        for p in parsed_data["paragraphs"]
        if not _is_footnote_paragraph(p, parsed_data["paragraphs"])
    ]
    chunks = _split_paragraphs(content_paras)
    if len(chunks) < 2:
        return None
    results = []
    for idx, chunk in enumerate(chunks):
        result = _extract_chunk(parsed_data, chunk, idx, grok_client)
        if result:
            results.append(result)
    if not results:
        return None

    # Validate completeness — if less than half of chunks succeeded, mark as partial
    if len(results) < len(chunks) // 2:
        logger.warning(
            "  Only %d/%d chunks extracted — skipping (too incomplete)",
            len(results),
            len(chunks),
        )
        return None

    merged = _merge_event_results(results)
    if require_coverage and _parts_leave_chapter_out(
        merged, parsed_data, chunks, grok_client
    ):
        return None
    _mark_partial(merged, len(results), len(chunks))
    return _save_event_output(merged, parsed_file, output_dir)


def _mark_partial(merged: dict, extracted: int, total: int) -> None:
    """Record (and log) how many of the chapter's parts the merged answer holds."""
    if extracted < total:
        merged["_partial"] = True
        merged["_chunks_extracted"] = extracted
        merged["_chunks_total"] = total
        logger.warning(
            "  Merged %d/%d chunks (partial — %d chunks failed)",
            extracted,
            total,
            total - extracted,
        )
    else:
        logger.info("  Merged %d chunks into single event", extracted)


def _parts_leave_chapter_out(merged, parsed_data, chunks, grok_client) -> bool:
    """Whether the merged parts still miss too much of the chapter (then their cached answers
    are evicted, so a re-run asks afresh)."""
    shortfall = _coverage_shortfall(merged, parsed_data)
    if not shortfall:
        return False
    logger.warning(
        "  The chapter's parts still cover only %.0f%% — not saved", shortfall[0] * 100
    )
    _evict_chunks(parsed_data, chunks, grok_client)
    return True


def _evict_chunks(parsed_data: dict, chunks: list, grok_client) -> None:
    """Drop the chunks' cached answers (a re-run must ask afresh, not replay them)."""
    system_prompt = get_system_prompt("events")
    for chunk in chunks:
        prompt = create_event_prompt({**parsed_data, "paragraphs": chunk})
        if prompt:
            grok_client.clear_cache_entry(
                prompt,
                cache_type="events",
                system_prompt=system_prompt,
                book=_book_of(parsed_data),
            )


def _valid_paragraph_set(parsed_data: dict) -> set:
    """The set of real paragraph absolute_numbers fed to the model (the valid citation domain)."""
    return {
        int(p["absolute_number"])
        for p in parsed_data.get("paragraphs", [])
        if isinstance(p, dict) and str(p.get("absolute_number", "")).isdigit()
    }


def _cited_paragraph_numbers(response: dict) -> list:
    """All integer paragraph_numbers the response's sub-events cite."""
    ev = response.get("Event", response)
    subs = ev.get("Sub-events", ev.get("Sub_events", [])) or []
    from src.utils.paragraph_numbers import coerce_paragraph_numbers

    nums: list = []
    for s in subs:
        if isinstance(s, dict):
            raw = s.get("paragraph_numbers") or s.get("paragraphs") or []
            nums += coerce_paragraph_numbers(raw, scope="events.cited")
    return nums


def _validate_paragraph_range(response: dict, parsed_data: dict, attempt: int) -> None:
    """Reject an events response whose sub-event paragraph_numbers fall mostly OUTSIDE the known
    input range (absolute_number 1..N). The valid answer is deterministically constrained by the
    paragraphs we fed the model; a hallucinated out-of-range set (e.g. 264..1700 for a 263-paragraph
    doc) resolves to no fulltext and starves every downstream entity extractor. Raises ValidationError
    (caught by the extract_events retry loop) so we re-submit with range feedback. Fail-safe: never
    raises on its own bookkeeping errors (only the deliberate ValidationError)."""
    from jsonschema import ValidationError

    try:
        valid = _valid_paragraph_set(parsed_data)
        nums = _cited_paragraph_numbers(response)
        if not valid or not nums:
            return  # no baseline, or nothing to validate (fulltext may come from another path)
        out_of_range = [n for n in nums if n not in valid]
        frac = len(out_of_range) / len(nums)
        max_valid = max(valid)
    except Exception:  # noqa: BLE001 - bookkeeping must never crash extraction
        return
    # A few stray refs are tolerable; a MAJORITY out-of-range is a hallucinated response.
    if frac < 0.5:
        return
    try:
        logger.warning(
            "OBSERV events.paragraph_range_invalid status=error attempt=%d out_of_range=%d/%d "
            "max_valid=%d sample=%s — rejecting + resubmitting (hallucinated paragraph_numbers "
            "would starve entity extraction)",
            attempt + 1,
            len(out_of_range),
            len(nums),
            max_valid,
            out_of_range[:5],
        )
    except Exception:  # noqa: BLE001
        pass
    raise ValidationError(
        f"paragraph_numbers out of range: {len(out_of_range)} of {len(nums)} cited numbers "
        f"are not among the {len(valid)} paragraphs provided (valid range 1..{max_valid}). "
        f"Cite ONLY the Paragraph_N numbers shown in the input."
    )


def extract_events(
    parsed_file: Path, grok_client: GrokClient, output_dir: Path, max_retries: int = 3
) -> Optional[Path]:
    """
    Extract events and sub-events from parsed content.

    Args:
        parsed_file: Path to parsed JSON file
        grok_client: Grok API client
        output_dir: Output directory
        max_retries: Maximum validation retry attempts

    Returns:
        Path to output file, or None if file was skipped
    """
    # Load parsed data
    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)

    _write_processing_summary(parsed_data)

    # Create prompt
    prompt = create_event_prompt(parsed_data)

    # Skip if no content (footnotes only)
    if prompt is None:
        logger.info(f"  Skipping {parsed_file.name}: contains only footnotes")
        return None

    original_prompt = prompt  # Keep original for cache key
    system_prompt = get_system_prompt("events")
    book = _book_of(parsed_data)
    shortfall = None  # the last answer's coverage shortfall, if it was too low

    # Try to get valid JSON with retries
    for attempt in range(max_retries):
        try:
            # Call Grok API
            response = grok_client.extract_json(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                use_cache=True,
                cache_type="events",
                book=book,
            )
        except GrokAPIError as e:
            if shortfall:  # a truncated re-ask of a chapter already found under-covered
                return _split_after_low_coverage(
                    parsed_data, parsed_file, output_dir, grok_client, shortfall
                )
            return _split_on_truncation(
                e, parsed_data, parsed_file, output_dir, grok_client
            )
        raw = json.dumps(response)  # as answered, before normalisation rewrites it

        # VALIDATE-AND-RESUBMIT (user 2026-10-30): the response's paragraph_numbers are
        # deterministically constrained by our input — they MUST be a subset of the paragraphs we
        # Validate the RAW response (paragraph_numbers in-range) BEFORE _normalize pops them, then
        # normalize + schema-validate — all under ONE retry handler. A hallucinated out-of-range
        # paragraph set (e.g. 264..1700 vs a 263-paragraph doc) would otherwise resolve to no
        # fulltext -> summary-only -> every entity extractor starves (the paragraph_numbers
        # fingerprint). On any ValidationError we RE-SUBMIT with feedback (or exhaust -> loud raise).
        try:
            _validate_paragraph_range(response, parsed_data, attempt)
            response = _normalize_event_response(response, parsed_data, parsed_file)
            validate_event_json(response)
            # A complete, valid answer can still leave most of the chapter in no sub-event
            # (Cross-Channel Attack ch. VIII, 2026-11-10: 7 sub-events held paragraphs 1-43 of 206,
            # and every entity of the other 163 was never extracted): reject it like an invalid
            # one - evict it, keep it for diagnosis, and ask again naming what was left out.
            shortfall = _coverage_shortfall(response, parsed_data)
            if shortfall:
                _reject_low_coverage(
                    grok_client,
                    prompt,
                    system_prompt,
                    raw,
                    parsed_file,
                    attempt,
                    shortfall,
                    book,
                )
                prompt = _coverage_retry_prompt(original_prompt, shortfall)
                continue
            # Validation passed - save and return
            return _save_accepted(
                response,
                parsed_file,
                output_dir,
                attempt,
                grok_client,
                (original_prompt, system_prompt, raw, book),
            )

        except ValidationError as e:
            fixed_output, prompt = _handle_validation_failure(
                response, e, parsed_file, output_dir, prompt, attempt, max_retries
            )
            if fixed_output:
                return fixed_output

    if shortfall:
        return _split_after_low_coverage(
            parsed_data, parsed_file, output_dir, grok_client, shortfall
        )
    # Should never reach here — all loop paths return or raise
    raise ValueError("Unexpected error in validation loop")


def _split_on_truncation(e, parsed_data, parsed_file, output_dir, grok_client) -> Path:
    """A truncated answer (the client asks for a split): extract the chapter in parts, else
    re-raise ``e``."""
    if "splitting chapter" not in str(e).lower():
        raise e
    logger.warning("  Response truncated — auto-splitting chapter")
    split_result = _auto_split_and_extract(
        parsed_data, parsed_file, output_dir, grok_client
    )
    if split_result:
        return split_result
    raise e


def _save_accepted(response, parsed_file, output_dir, attempt, grok_client, asked):
    """Save the accepted answer. After a retry, also store it under the ORIGINAL request's key
    (``asked`` = original prompt, system prompt, answer as received, book), so a re-run of the chapter
    is served it instead of re-asking - and re-rejecting - the first answer."""
    output_file = _save_event_output(response, parsed_file, output_dir)
    if attempt > 0:
        logger.info(f"  Validation passed on attempt {attempt + 1}")
        _cache_answer(grok_client, *asked)
    return output_file


def _coverage_shortfall(response: dict, parsed_data: dict) -> Optional[tuple]:
    """(share, uncovered paragraph numbers) when the sub-events hold less than _MIN_COVERAGE of
    the chapter's narrative, else None (also for a chapter with no narrative)."""
    found = _coverage_of(response, parsed_data.get("paragraphs", []) or [])
    if found is None or found[0] >= _MIN_COVERAGE:
        return None
    return found[0], found[2]


def _reject_low_coverage(
    grok_client, prompt, system_prompt, raw, parsed_file, attempt, shortfall, book=None
) -> None:
    """Evict the incomplete answer (with the cache on it would be served to every re-run), keep a
    copy beside the cache for diagnosis (uploaded with it to diagnostics/cache/<book>/), log it.
    """
    share, missing = shortfall
    evicted = grok_client.clear_cache_entry(
        prompt, cache_type="events", system_prompt=system_prompt, book=book
    )
    kept = _keep_rejected(grok_client, parsed_file, attempt, raw, share, missing)
    logger.warning(
        "OBSERV events.coverage_rejected status=warn file=%s attempt=%d share=%.3f "
        "uncovered=%s evicted=%s kept=%s — asking again with the uncovered paragraphs named",
        parsed_file.name,
        attempt + 1,
        share,
        _ranges(missing)[:20],
        evicted,
        kept or "-",
    )


def _keep_rejected(grok_client, parsed_file, attempt, raw, share, missing) -> str:
    """Write the rejected answer under <cache dir>/rejected/<book>/ ("" if there is no cache dir
    or the write fails: diagnostics never break extraction)."""
    try:
        base = getattr(grok_client, "cache_dir", None)
        if base is None:
            return ""
        out = (
            Path(base)
            / "rejected"
            / parsed_file.parent.name
            / f"{parsed_file.stem}-coverage-attempt{attempt + 1}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "reason": "coverage_low",
                    "share": round(share, 3),
                    "uncovered": _ranges(missing),
                    "answer": json.loads(raw),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return str(out)
    except Exception as e:  # noqa: BLE001 - diagnostics only
        logger.warning(
            "Could not keep the rejected events answer: %s", type(e).__name__
        )
        return ""


def _coverage_retry_prompt(original_prompt: str, shortfall: tuple) -> str:
    """The original request, with what the previous answer left out (always built on the
    original, so feedback never piles up over attempts)."""
    share, missing = shortfall
    return f"""{original_prompt}

PREVIOUS ATTEMPT WAS INCOMPLETE:
Your sub-events held only {share:.0%} of the chapter's narrative. These paragraphs are in NO
sub-event: {", ".join(_ranges(missing)[:40])}.
Every narrative paragraph must belong to exactly one sub-event. Read the WHOLE chapter, to its last
paragraph, and return the complete set of sub-events.
"""


def _cache_answer(
    grok_client,
    prompt: str,
    system_prompt: Optional[str],
    raw: str,
    book: Optional[str] = None,
) -> None:
    """Store ``raw`` as the answer to ``prompt``, stamped with THAT request's identity (the answer
    came from a re-ask, which carried its own RequestID; unstamped, a re-run would reject it).
    """
    grok_client.store_json_answer(
        prompt, system_prompt, "events", json.loads(raw), temperature=0.1, book=book
    )


def _split_after_low_coverage(
    parsed_data, parsed_file, output_dir, grok_client, shortfall
) -> Path:
    """Every attempt left too much of the chapter out: extract it in halves (each a shorter
    request). If that does not cover it either, fail the chapter LOUDLY - never publish a chapter
    whose entities come from a fraction of it."""
    logger.warning(
        "  Events cover only %.0f%% after every attempt — extracting the chapter in parts",
        shortfall[0] * 100,
    )
    error = None
    try:
        out = _auto_split_and_extract(
            parsed_data, parsed_file, output_dir, grok_client, require_coverage=True
        )
    except Exception as e:  # noqa: BLE001 - re-raised below, after the canonical alert
        out, error = None, e
    if out:
        return out
    from src.utils.observe import observe_error

    observe_error(
        "events",
        "coverage_unrecoverable",
        note=(
            f"every events answer for this chapter held only {shortfall[0]:.0%} of its narrative, "
            "also when extracted in parts: NO event file was written (entities of this chapter are "
            "not extracted). The rejected answers are under diagnostics/cache/<book>/rejected/; "
            "re-run the chapter's Phase 2 after reviewing them"
        ),
        file=parsed_file.name,
        share=round(shortfall[0], 3),
        uncovered=_ranges(shortfall[1])[:20],
        split_error=type(error).__name__ if error else "",
    )
    if error is not None:
        raise error
    raise ValueError(
        f"events for {parsed_file.name} cover only {shortfall[0]:.0%} of the chapter"
    )


def _normalize_event_response(response, parsed_data, parsed_file):
    """Normalize a raw events response before schema validation: flat-wrap ({EventID,...} ->
    {Event:{...}}), ensure Chapter, populate per-sub-event footnote/endnote refs (BEFORE
    _reconstruct_fulltext, which pops the paragraph arrays it needs), reconstruct fulltext, OBSERV
    the ref population, then swap LLM ids for deterministic content-derived ids. Returns response.
    """
    # Wrap flat response (AI returns {EventID, Sub-events} instead of {Event: {...}}).
    if "EventID" in response and "Event" not in response:
        response = {"Event": response}
    # Ensure Chapter is present (required by schema, may not be in AI response).
    if "Chapter" not in response:
        response["Chapter"] = parsed_data.get("chapter_title", "")
    # Populate refs BEFORE _reconstruct_fulltext (which pops the 'paragraphs' arrays it needs).
    # Each paragraph belongs to ONE sub-event first (overlapping claims duplicated every entity).
    _make_paragraphs_disjoint(response, parsed_data)
    _populate_footnote_refs(response, parsed_data)
    _reconstruct_fulltext(response, parsed_data)
    # OBSERV (bibliography chain): did we actually populate refs on the sub-events?
    try:
        subs = response.get("Event", {}).get("Sub-events", [])
        with_refs = sum(
            1
            for s in subs
            if isinstance(s, dict)
            and (s.get("Footnote_References") or s.get("Endnote_References"))
        )
        logger.info(
            "OBSERV events.refs file=%s sub_events=%d with_refs=%d parsed_footnotes=%d",
            parsed_file.name,
            len(subs),
            with_refs,
            len(parsed_data.get("footnotes", [])),
        )
    except Exception:  # noqa: BLE001 - observability must never break extraction
        pass
    # Replace LLM-supplied event ids with deterministic, content-derived ids (idempotent across
    # re-runs → no cross-reference ripple; the model's id is derived, not trusted).
    _apply_deterministic_ids(response, parsed_data)
    return response


def _handle_validation_failure(
    response, e, parsed_file, output_dir, prompt, attempt, max_retries
):
    """Handle a schema ValidationError inside the extract retry loop. Returns
    (output_path_or_None, next_prompt): a non-None output means a ULID-autofix recovered it;
    otherwise builds the validation-feedback retry prompt, or raises on the final attempt.
    """
    # Try to fix ULID errors automatically.
    fixed_output = _try_fix_ulid_errors(response, e, parsed_file, output_dir)
    if fixed_output:
        return fixed_output, prompt

    error_msg = f"Validation error: {e.message}\nPath: {e.json_path}"
    logger.warning(f"  Attempt {attempt + 1}/{max_retries} failed: {e.message}")

    if attempt < max_retries - 1:
        logger.info("  Retrying with validation feedback...")
        ulid_error = f"does not match '{ULID_PATTERN}'" in e.message
        return None, _build_retry_prompt(prompt, error_msg, ulid_error)

    logger.error(f"  Validation failed after {max_retries} attempts")
    logger.error(f"  Final error: {e.message}")
    raise ValueError(
        f"JSON validation failed after {max_retries} attempts: {e.message}"
    ) from e
