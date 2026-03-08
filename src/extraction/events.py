"""Event and sub-event extraction from parsed content."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

from jsonschema import ValidationError, validate
import ulid

from src.grok_client import GrokClient
from src.json_schemas import EVENT_SCHEMA

logger = logging.getLogger(__name__)


def _fix_invalid_ulids(
    data: Union[Dict[str, Any], list],
) -> Union[Dict[str, Any], list]:
    """
    Recursively fix invalid ULIDs in the response.

    Replaces any string that looks like a ULID but has invalid characters
    with a properly generated ULID.
    """
    # ULID regex pattern
    ulid_pattern = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

    if isinstance(data, dict):
        for key, value in data.items():
            if key in ["EventID", "Sub-eventID"] and isinstance(value, str):
                # Check if it's supposed to be a ULID but is invalid
                if not ulid_pattern.match(value):
                    # Generate a valid ULID
                    new_ulid = str(ulid.new())
                    data[key] = new_ulid
                    logger.debug(f"  Replaced invalid ULID '{value}' with '{new_ulid}'")
            elif isinstance(value, (dict, list)):
                data[key] = _fix_invalid_ulids(value)
    elif isinstance(data, list):
        return [_fix_invalid_ulids(item) for item in data]

    return data


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
    Validate event JSON against schema.

    Raises:
        ValidationError: If JSON doesn't match schema
    """
    validate(instance=data, schema=EVENT_SCHEMA)


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
        "Sub-event_fulltext": {{
          "Paragraph_1": "exact text",
          "Paragraph_2": "exact text"
        }},
        "Endnote_References": [1, 2],
        "Footnote_References": ["*", "†"]
      }}
    ]
  }}
}}

OTHER REQUIREMENTS:
- Group related paragraphs into sub-events
- Use absolute paragraph numbers (Paragraph_N)
- Preserve exact paragraph text
- Extract endnote numbers and footnote symbols from the text
"""

    return prompt


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

    # Write processing summary to log
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

    # Create prompt
    prompt = create_event_prompt(parsed_data)

    # Skip if no content (footnotes only)
    if prompt is None:
        logger.info(f"  Skipping {parsed_file.name}: contains only footnotes")
        return None

    original_prompt = prompt  # Keep original for cache key

    # Try to get valid JSON with retries
    for attempt in range(max_retries):
        # Call Grok API
        response = grok_client.extract_json(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            use_cache=True,
            cache_type="events",  # Separate cache for events
        )

        # Validate against schema
        try:
            validate_event_json(response)
            # Validation passed - save and return
            output_file = output_dir / parsed_file.name.replace(
                "-parsed.json", "-event.json"
            )
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(response, f, indent=2, ensure_ascii=False)

            if attempt > 0:
                logger.info(f"  Validation passed on attempt {attempt + 1}")
                # Cache the successful result with original prompt
                # so future runs skip failed attempts
                cache = grok_client._get_cache("events")
                cache[grok_client._make_cache_key(original_prompt, 0.1)] = json.dumps(
                    response
                )

            return output_file

        except ValidationError as e:
            # Check if it's only a ULID error - if so, fix it
            if "does not match '^[0-9A-HJKMNP-TV-Z]{26}$'" in e.message:
                try:
                    fixed_response = _fix_invalid_ulids(response)
                    if isinstance(fixed_response, dict):
                        validate_event_json(fixed_response)
                        # Fixed validation passed - save and return
                        logger.info("Fixed invalid ULIDs automatically")
                        output_file = output_dir / parsed_file.name.replace(
                            "-parsed.json", "-event.json"
                        )
                        with open(output_file, "w", encoding="utf-8") as f:
                            json.dump(fixed_response, f, indent=2, ensure_ascii=False)
                        return output_file
                except Exception:
                    pass  # Fall through to normal retry logic
            error_msg = f"Validation error: {e.message}\nPath: {e.json_path}"
            logger.warning(f"  Attempt {attempt + 1}/{max_retries} failed: {e.message}")

            if attempt < max_retries - 1:
                # Add validation error to prompt and retry
                logger.info("  Retrying with validation feedback...")

                # Check if it's a ULID error
                ulid_error = "does not match '^[0-9A-HJKMNP-TV-Z]{26}$'" in e.message
                ulid_warning = (
                    "\n\nYou are not correctly creating ULIDs. Please correct and review the included specification: https://github.com/ulid/spec\nRemember: NO SPACES, exactly 26 characters, only use 0-9 A-H J-K M-N P-T V-Z"
                    if ulid_error
                    else ""
                )

                prompt = f"""{prompt}

PREVIOUS ATTEMPT FAILED VALIDATION:
{error_msg}{ulid_warning}

Please fix the JSON to match the schema exactly. Ensure:
- All required fields are present
- Paragraph keys use format "Paragraph_N" where N is the number
- Images and Maps are arrays of [url, description] pairs
- References are arrays of integers
"""
            else:
                # Final attempt failed
                logger.error(f"  Validation failed after {max_retries} attempts")
                logger.error(f"  Final error: {e.message}")
                raise ValueError(
                    f"JSON validation failed after {max_retries} attempts: {e.message}"
                ) from e

    # Log extraction summary with paragraph groupings
    extraction_summary = {
        "book": parsed_data["book"],
        "chapter": parsed_data["chapter_title"],
        "section": parsed_data.get("section", "full"),
        "event_id": response.get("Event", {}).get("EventID", ""),
        "sub_events": [],
    }

    for sub_event in response.get("Event", {}).get("Sub-events", []):
        paragraphs = list(sub_event.get("Sub-event_fulltext", {}).keys())
        para_numbers = [int(p.split("_")[1]) for p in paragraphs if "_" in p]
        extraction_summary["sub_events"].append(
            {
                "sub_event_id": sub_event.get("Sub-eventID", ""),
                "summary": sub_event.get("Sub-event_summary", ""),
                "paragraphs": sorted(para_numbers),
            }
        )

    summary_log = Path("logs") / "extraction_summary.json"
    with open(summary_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(extraction_summary) + "\n")

    # Should never reach here
    raise ValueError("Unexpected error in validation loop")
