"""Date extraction from event data with central repository."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

import ulid
from pydantic import BaseModel, ConfigDict, Field

from src.grok_client import GrokClient
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)


# Pydantic schemas for structured outputs
class DateMention(BaseModel):
    """Date mention extracted from event text."""

    DateMentionID: str = Field(description="26-character ULID")
    date_start: str = Field(
        description="ISO format (YYYY-MM-DD) or approximate (mid-1944-07, summer-1942)"
    )
    date_end: Optional[str] = Field(default=None, description="End date if range")
    time_start: Optional[str] = Field(default=None, description="Start time HH:MM")
    time_end: Optional[str] = Field(default=None, description="End time HH:MM")
    time_precision: Optional[str] = Field(
        default=None, description="exact or approximate"
    )
    date_precision: Optional[str] = Field(
        default=None,
        description="exact, early, mid, late, spring, summer, fall, winter",
    )
    time_source: Optional[str] = Field(
        default=None, description="German, Allied, Zulu, etc."
    )
    original_text: str = Field(description="Exact text from document")


class DateOutput(BaseModel):
    """LLM output structure for date extraction."""

    Event_Name: str = Field(description="Name of the event")
    EventID: str = Field(description="26-character ULID of event")
    Sub_event_Name: str = Field(
        description="Name of the sub-event", alias="Sub-event_Name"
    )
    Sub_eventID: str = Field(
        description="26-character ULID of sub-event", alias="Sub-eventID"
    )
    Date_Mentions: list[DateMention] = Field(description="List of date mentions")

    model_config = ConfigDict(populate_by_name=True)


SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract all date and time mentions from the provided event text.

CRITICAL RULES:
1. You MUST complete the entire JSON response. Do NOT stop until all closing braces and brackets are in place.
2. Return ONLY valid, complete JSON. Ensure all arrays and objects are properly closed.
3. ONLY extract dates you can parse into a specific format (ISO or approximate).
4. If you cannot determine a date_start value, OMIT that mention entirely.
5. Do NOT include mentions with null, empty, or missing date_start fields.
6. Every mention MUST have both date_start and original_text populated.

Return structured data matching the schema."""


def _fix_invalid_ulids(
    data: Union[Dict[str, Any], list],
) -> Union[Dict[str, Any], list]:
    """Replace invalid ULIDs with valid ones."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("ID") and isinstance(value, str):
                # Check if ULID is valid (exactly 26 chars, valid charset)
                if len(value) != 26 or not all(
                    c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in value
                ):
                    new_ulid = str(ulid.new())
                    data[key] = new_ulid
                    logger.debug(
                        "  Fixed invalid ULID in %s: '%s' (%d chars) -> '%s' (26 chars)",
                        key,
                        value,
                        len(value),
                        new_ulid,
                    )
            elif isinstance(value, (dict, list)):
                data[key] = _fix_invalid_ulids(value)
    elif isinstance(data, list):
        return [_fix_invalid_ulids(item) for item in data]
    return data


def _filter_invalid_dates(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove date mentions with missing required fields."""
    if "Date_Mentions" in data and isinstance(data["Date_Mentions"], list):
        original_count = len(data["Date_Mentions"])
        valid_dates = []
        for mention in data["Date_Mentions"]:
            if isinstance(mention, dict):
                # Check required fields
                if not mention.get("date_start"):
                    logger.warning(
                        "  Filtered date mention with null date_start: %s",
                        mention.get("original_text", "unknown"),
                    )
                    continue
                if not mention.get("original_text"):
                    logger.warning("  Filtered date mention with null original_text")
                    continue
                valid_dates.append(mention)

        filtered_count = original_count - len(valid_dates)
        if filtered_count > 0:
            logger.info("  Filtered %d invalid date mention(s)", filtered_count)

        data["Date_Mentions"] = valid_dates
    return data


def create_date_prompt(
    sub_event: Dict[str, Any], event_id: str, event_name: str
) -> str:
    """Create prompt for date extraction from a sub-event."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text = "\n".join(fulltext.values())

    prompt = f"""Extract ALL date and time mentions from this WWII event text.

Event: {event_name}
EventID: {event_id}
Sub-event: {sub_event_summary}
Sub-eventID: {sub_event_id}

Text:
{text}

Return JSON matching this structure:
{{
  "Event_Name": "{event_name}",
  "EventID": "{event_id}",
  "Sub_event_Name": "{sub_event_summary}",
  "Sub_eventID": "{sub_event_id}",
  "Date_Mentions": [
    {{
      "DateMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
      "date_start": "1944-07-01",
      "date_end": null,
      "time_start": "14:30",
      "time_end": null,
      "time_precision": "exact",
      "date_precision": "exact",
      "time_source": "Allied",
      "original_text": "1 July 1944 at 1430 hours"
    }}
  ]
}}

Instructions:
- For exact dates: use ISO format (1944-07-15)
- For approximate dates: use prefix format (mid-1944-07, early-1944, late-1944-06, summer-1942, winter-1944)
- date_precision: "exact", "early", "mid", "late", "spring", "summer", "fall", "autumn", or "winter"
- time_precision: "exact" or "approximate"

Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
If no dates found, return empty Date_Mentions array."""

    return prompt


def _normalize_date_key(date_start: str, time_start: Optional[str] = None) -> str:
    """Create normalized key for date lookup (sortable format)."""
    # Convert approximate dates to sortable format
    # early-1942-02 → 1942-02-early
    # summer-1942 → 1942-summer
    prefixes = (
        "early-",
        "mid-",
        "late-",
        "spring-",
        "summer-",
        "fall-",
        "autumn-",
        "winter-",
    )

    for prefix in prefixes:
        if date_start.startswith(prefix):
            precision = prefix.rstrip("-")  # early, mid, late, summer, etc.
            date_part = date_start[len(prefix) :]  # 1942-02 or 1942
            date_key = f"{date_part}-{precision}"
            break
    else:
        date_key = date_start

    if time_start:
        return f"{date_key}T{time_start}"
    return date_key


def _find_or_create_date(
    mention: Dict[str, Any], dates_dir: Path, index: Dict[str, str]
) -> Path:
    """Find existing date file or create new one."""
    date_start = mention.get("date_start", "")
    time_start = mention.get("time_start")

    # Create lookup key
    date_key = _normalize_date_key(date_start, time_start)

    # Check index
    if date_key in index:
        return dates_dir / index[date_key]

    # Create new date file
    date_id = str(ulid.new())

    # Create safe filename from date
    safe_date = (
        date_start.replace("-", "")
        .replace("early", "E")
        .replace("mid", "M")
        .replace("late", "L")
        .replace("spring", "SP")
        .replace("summer", "SU")
        .replace("fall", "FA")
        .replace("autumn", "AU")
        .replace("winter", "WI")
    )
    if time_start:
        safe_time = time_start.replace(":", "")
        filename = f"{safe_date}_{safe_time}_{date_id[:8]}.json"
    else:
        filename = f"{safe_date}_{date_id[:8]}.json"

    date_file = dates_dir / filename

    # Initialize date file
    date_data = {
        "DateID": date_id,
        "date_start": mention.get("date_start"),
        "date_end": mention.get("date_end"),
        "time_start": mention.get("time_start"),
        "time_end": mention.get("time_end"),
        "time_precision": mention.get("time_precision"),
        "date_precision": mention.get("date_precision"),
        "time_source": mention.get("time_source"),
        "original_text": mention.get("original_text", ""),
        "normalized_datetime": None,  # TODO: Implement normalization
        "event_mentions": [],
    }

    write_json_with_lock(date_file, date_data)

    # Update index
    index[date_key] = filename

    logger.info("    Created date file: %s", filename)
    return date_file


def _add_event_mention(
    date_file: Path,
    mention: Dict[str, Any],
    event_name: str,
    event_id: str,
    sub_event_name: str,
    sub_event_id: str,
    book: str,
    author: str,
    series: str,
) -> None:
    """Add event mention to existing date file."""
    with open(date_file, "r", encoding="utf-8") as f:
        date_data = json.load(f)

    # Create event mention
    event_mention = {
        "MentionID": str(ulid.new()),
        "Event_Name": event_name,
        "EventID": event_id,
        "Sub_event_Name": sub_event_name,
        "Sub_eventID": sub_event_id,
        "book": book,
        "author": author,
        "series": series,
        "context": None,  # TODO: Extract from text
        "original_text": mention.get("original_text", ""),
    }

    # Check for duplicate mention (same sub-event)
    existing = [
        m for m in date_data["event_mentions"] if m["Sub_eventID"] == sub_event_id
    ]
    if existing:
        logger.info("    Date already has mention from this sub-event, skipping")
        return

    # Add mention
    date_data["event_mentions"].append(event_mention)

    # Save updated file
    write_json_with_lock(date_file, date_data)

    logger.info("    Added mention to %s", date_file.name)


def extract_dates(
    event_file: Path,
    grok_client: GrokClient,
    dates_dir: Path,
    parsed_file: Optional[Path] = None,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract dates from event file and add to central repository.

    Args:
        event_file: Path to event JSON file
        grok_client: Grok API client
        dates_dir: Central dates directory (output/dates/)
        parsed_file: Path to parsed JSON file (for book metadata)
        max_retries: Maximum retry attempts per sub-event

    Returns:
        Path to dates directory, or None if failed
    """
    dates_dir.mkdir(parents=True, exist_ok=True)
    index_file = dates_dir / "index.json"

    # Load existing index
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Get book metadata from parsed file if provided
    book = ""
    author = ""
    series = ""
    if parsed_file and parsed_file.exists():
        with open(parsed_file, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)
            book = parsed_data.get("book", "")
            author = parsed_data.get("author", "")
            series = parsed_data.get("series", "")

    # Validate required metadata
    if not book or not author:
        raise ValueError(
            f"Missing required book metadata in {parsed_file}: "
            f"book={book!r}, author={author!r}"
        )

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    dates_updated = 0

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_name = sub_event.get("Sub-event_summary", "")
        logger.info("  Processing sub-event %s", sub_event_id)

        prompt = create_date_prompt(sub_event, event_id, event_name)

        # Retry logic for truncated responses
        for attempt in range(max_retries):
            try:
                date_output = grok_client.extract_structured(
                    prompt=prompt,
                    schema=DateOutput,
                    system_prompt=SYSTEM_PROMPT,
                    use_cache=(attempt == 0),
                    cache_type="dates",
                )

                date_dict: Dict[str, Any] = date_output.model_dump(by_alias=True)
                if not isinstance(date_dict, dict):
                    continue
                fixed_dict = _fix_invalid_ulids(date_dict)
                if not isinstance(fixed_dict, dict):
                    continue
                date_dict = _filter_invalid_dates(fixed_dict)

                # Process each date mention
                for mention in date_dict.get("Date_Mentions", []):
                    date_file = _find_or_create_date(mention, dates_dir, index)
                    _add_event_mention(
                        date_file,
                        mention,
                        event_name,
                        event_id,
                        sub_event_name,
                        sub_event_id,
                        book,
                        author,
                        series,
                    )
                    dates_updated += 1

                num_dates = len(date_dict.get("Date_Mentions", []))
                logger.info("  ✓ Extracted %d dates", num_dates)
                break  # Success, exit retry loop

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("  ⚠ Attempt %d failed: %s", attempt + 1, e)
                    logger.info("  Retrying (%d/%d)...", attempt + 2, max_retries)
                else:
                    logger.error("  ✗ All %d attempts failed: %s", max_retries, e)
                    continue

    # Save index
    write_json_with_lock(index_file, index)

    logger.info("Updated %d date mentions in central repository", dates_updated)
    return dates_dir if dates_updated > 0 else None
