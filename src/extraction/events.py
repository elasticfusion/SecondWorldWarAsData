"""Event and sub-event extraction from parsed content."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import ValidationError, validate

from src.grok_client import GrokClient, GrokAPIError
from src.json_schemas import EVENT_SCHEMA
from src.utils.json_validator import _fix_invalid_ulids

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


def _reconstruct_fulltext(response: dict, parsed_data: dict) -> dict:
    """Replace 'paragraphs' number arrays with 'Sub-event_fulltext' dicts.

    Looks up paragraph text from parsed_data by absolute_number.
    """
    para_lookup = {
        p["absolute_number"]: p["text"] for p in parsed_data.get("paragraphs", [])
    }
    for se in response.get("Event", {}).get("Sub-events", []):
        nums = se.pop("paragraphs", None)
        if nums is None and "Sub-event_fulltext" in se:
            continue  # Already has fulltext (cached old-format response)
        fulltext = {}
        for n in nums or []:
            if isinstance(n, int) and n in para_lookup:
                fulltext[f"Paragraph_{n}"] = para_lookup[n]
            else:
                logger.warning("  Paragraph %s not found in parsed data, skipping", n)
        se["Sub-event_fulltext"] = fulltext

        # Fallback: if fulltext is empty but summary exists, use summary as text
        if not fulltext and se.get("Sub-event_summary"):
            logger.debug(
                "Sub-event %s has summary but no fulltext — using summary as fallback",
                se.get("Sub-eventID"),
            )
            se["Sub-event_fulltext"] = {"summary": se["Sub-event_summary"]}
    return response


def _int_number_set(items) -> set:
    """Collect the integer ``number`` fields from a list of parsed note dicts."""
    return {
        int(x.get("number"))
        for x in (items or [])
        if isinstance(x, dict) and isinstance(x.get("number"), int)
    }


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
        return (
            para_lookup,
            _int_number_set(parsed_data.get("footnotes")),
            _int_number_set(parsed_data.get("endnotes")),
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - bad parsed arrays -> no classification context
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


def _apply_deterministic_ids(response: dict, parsed_data: dict) -> dict:
    """Replace the LLM-supplied EventID/Sub-eventIDs with DETERMINISTIC, content-derived ids so a
    re-extraction of the same source unit yields identical ids (no cross-reference ripple), and the
    model's id is derived/validated rather than trusted. Central: ids come from
    ``src.utils.deterministic_ids``. Called after fulltext reconstruction, before validation.
    """
    from src.utils.deterministic_ids import event_id as _event_id
    from src.utils.deterministic_ids import sub_event_id as _sub_event_id

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
        se["Sub-eventID"] = _sub_event_id(
            eid,
            para_start=start,
            para_end=end,
            name=name,
        )
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
            logger.debug("source_section: source metadata unavailable: %s", meta_err)
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
    )
    if "EventID" in result and "Event" not in result:
        result = {"Event": result}
    # Blocker C (both paths): populate footnote refs from the chunk's raw paragraphs
    # BEFORE _reconstruct_fulltext pops them. Without this, auto-split chapters yield
    # sub-events with no Footnote_References -> 0 bibliography for split documents.
    _populate_footnote_refs(result, chunk_data)
    _reconstruct_fulltext(result, chunk_data)
    _apply_deterministic_ids(result, chunk_data)
    validate_event_json(result)
    return result


def _try_fix_ulid_errors(
    response: dict, e: ValidationError, parsed_file: Path, output_dir: Path
) -> Optional[Path]:
    """Try to fix ULID validation errors automatically."""
    if "does not match '^[0-9A-HJKMNP-TV-Z]{26}$'" not in e.message:
        return None

    try:
        fixed_response = _fix_invalid_ulids(response)
        if isinstance(fixed_response, dict):
            validate_event_json(fixed_response)
            logger.info("Fixed invalid ULIDs automatically")
            return _save_event_output(fixed_response, parsed_file, output_dir)
    except Exception as e:
        logger.warning("Event extraction step failed: %s", e)

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
) -> Optional[Path]:
    """Split a too-large chapter into chunks, extract each, merge results."""
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
    if len(results) < len(chunks):
        merged["_partial"] = True
        merged["_chunks_extracted"] = len(results)
        merged["_chunks_total"] = len(chunks)
        logger.warning(
            "  Merged %d/%d chunks (partial — %d chunks failed)",
            len(results),
            len(chunks),
            len(chunks) - len(results),
        )
    else:
        logger.info("  Merged %d chunks into single event", len(results))
    return _save_event_output(merged, parsed_file, output_dir)


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

    # Try to get valid JSON with retries
    for attempt in range(max_retries):
        try:
            # Call Grok API
            response = grok_client.extract_json(
                prompt=prompt,
                system_prompt=get_system_prompt("events"),
                temperature=0.1,
                use_cache=True,
                cache_type="events",
            )
        except GrokAPIError as e:
            if "splitting chapter" not in str(e).lower():
                raise
            logger.warning("  Response truncated — auto-splitting chapter")
            split_result = _auto_split_and_extract(
                parsed_data, parsed_file, output_dir, grok_client
            )
            if split_result:
                return split_result
            raise

        response = _normalize_event_response(response, parsed_data, parsed_file)

        # Validate against schema
        try:
            validate_event_json(response)
            # Validation passed - save and return
            output_file = _save_event_output(response, parsed_file, output_dir)

            if attempt > 0:
                logger.info(f"  Validation passed on attempt {attempt + 1}")
                # Cache the successful result with original prompt
                cache = grok_client._get_cache("events")
                cache[grok_client._make_cache_key(original_prompt, 0.1)] = json.dumps(
                    response
                )

            return output_file

        except ValidationError as e:
            fixed_output, prompt = _handle_validation_failure(
                response, e, parsed_file, output_dir, prompt, attempt, max_retries
            )
            if fixed_output:
                return fixed_output

    # Should never reach here — all loop paths return or raise
    raise ValueError("Unexpected error in validation loop")


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
        ulid_error = "does not match '^[0-9A-HJKMNP-TV-Z]{26}$'" in e.message
        return None, _build_retry_prompt(prompt, error_msg, ulid_error)

    logger.error(f"  Validation failed after {max_retries} attempts")
    logger.error(f"  Final error: {e.message}")
    raise ValueError(
        f"JSON validation failed after {max_retries} attempts: {e.message}"
    ) from e
