"""Event and sub-event extraction from parsed content."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import ValidationError, validate
import ulid

from src.grok_client import GrokClient, GrokAPIError
from src.json_schemas import EVENT_SCHEMA
from src.utils.json_validator import _fix_invalid_ulids

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract events and sub-events from the provided text.

Requirements:
- Group related paragraphs into logical sub-events
- Each sub-event should have a clear summary
- Preserve the exact paragraph text with absolute paragraph numbers
- Extract images, maps, dates, places mentioned in each sub-event
- Identify endnote and footnote references

Return ONLY valid JSON matching the schema. No additional text."""


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

    prompt = f"""CRITICAL ULID REQUIREMENTS - READ FIRST:
- ULID Specification: https://github.com/ulid/spec
- EventID and Sub-eventID MUST be exactly 26 characters long
- Use only these characters: 0-9 A-H J-K M-N P-T V-Z (NO I, L, O, U)
- NO SPACES allowed in ULIDs
- Valid example: 01KHXNSE0W41DV7VV6PEMDJJ5H (26 chars, no spaces, no I/L/O/U)
- Generate NEW unique ULIDs - do not reuse the examples above

Analyze this chapter from "{book}" by {author}.

Chapter: {chapter}

Paragraphs:
{paragraphs}

Available Images:
{images_text}

Available Maps:
{maps_text}

Extract the main event and sub-events. Return JSON in this exact format:
{{
  "Chapter": "Chapter title",
  "Event": {{
    "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
    "Sub-events": [
      {{
        "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
        "Sub-event_summary": "Brief summary",
        "paragraphs": [1, 2],
        "Endnote_References": [1, 2],
        "Footnote_References": ["*", "†"]
      }}
    ]
  }}
}}

OTHER REQUIREMENTS:
- Group related paragraphs into sub-events
- "paragraphs" is an array of absolute paragraph NUMBERS only (integers) — do NOT include paragraph text
- Extract endnote numbers and footnote symbols from the text
"""

    try:
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "events",
            book=book,
            author=author,
            chapter=chapter,
            paragraphs=paragraphs,
            images_text=images_text,
            maps_text=maps_text,
        )
    except Exception as e:
        logger.warning("Event extraction step failed: %s", e)
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
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(response, f, indent=2, ensure_ascii=False)
    return output_file


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
    """Merge multiple event extraction results into one, combining sub-events."""
    base = results[0]
    for extra in results[1:]:
        extra_subs = extra.get("Event", {}).get("Sub-events", [])
        base.setdefault("Event", {}).setdefault("Sub-events", []).extend(extra_subs)
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
        system_prompt=SYSTEM_PROMPT,
        temperature=0.1,
        use_cache=True,
        cache_type="events",
    )
    _reconstruct_fulltext(result, chunk_data)
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
                system_prompt=SYSTEM_PROMPT,
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

        # Reconstruct fulltext from parsed data before validation
        _reconstruct_fulltext(response, parsed_data)

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
            # Try to fix ULID errors automatically
            fixed_output = _try_fix_ulid_errors(response, e, parsed_file, output_dir)
            if fixed_output:
                return fixed_output

            error_msg = f"Validation error: {e.message}\nPath: {e.json_path}"
            logger.warning(f"  Attempt {attempt + 1}/{max_retries} failed: {e.message}")

            if attempt < max_retries - 1:
                logger.info("  Retrying with validation feedback...")
                ulid_error = "does not match '^[0-9A-HJKMNP-TV-Z]{26}$'" in e.message
                prompt = _build_retry_prompt(prompt, error_msg, ulid_error)
            else:
                # Final attempt failed
                logger.error(f"  Validation failed after {max_retries} attempts")
                logger.error(f"  Final error: {e.message}")
                raise ValueError(
                    f"JSON validation failed after {max_retries} attempts: {e.message}"
                ) from e

    # Should never reach here — all loop paths return or raise
    raise ValueError("Unexpected error in validation loop")
