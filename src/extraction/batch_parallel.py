"""Batch and parallel extraction for maximum performance."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


async def process_chapter_async(
    parsed_file: Path,
    event_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Process single chapter: events + entities in parallel."""
    # If event file doesn't exist, extract events first
    if not event_file.exists():
        from src.extraction.events import extract_events

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: extract_events(parsed_file, grok_client, parsed_file.parent)
        )

    # Extract all entities in parallel
    return await extract_all_async(
        event_file=event_file,
        parsed_file=parsed_file,
        grok_client=grok_client,
        output_root=output_root,
        config=config,
    )


def _create_chapter_tasks(
    batch: List[Path],
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> List[tuple]:
    """Create async tasks for a batch of chapters."""
    tasks = []
    for pf in batch:
        stem = pf.stem.replace("-parsed", "")
        event_file = pf.parent / f"{stem}-event.json"

        task = process_chapter_async(
            parsed_file=pf,
            event_file=event_file,
            grok_client=grok_client,
            output_root=output_root,
            config=config,
        )
        tasks.append((pf.name, task))

    return tasks


def _get_cache_clear_command(name: str, error_msg: str) -> str:
    """Generate cache clearing command based on error type."""
    chapter_id = name.replace("-parsed.json", "")

    if any(
        keyword in error_msg.lower()
        for keyword in ["invalid", "escape", "control character", "unterminated string"]
    ):
        return (
            f'python3 -c "from pathlib import Path; from diskcache import Cache; '
            f"c=Cache('cache/api/events'); "
            f"[c.pop(k) for k in list(c) if '{chapter_id}' in str(c.get(k, ''))]\""
        )

    return "rm -rf cache/api/*"


def _process_batch_results(
    tasks: List[tuple], batch_results: list, results: Dict[str, Any]
) -> None:
    """Process results from a batch of chapters."""
    for (name, _), result in zip(tasks, batch_results):
        if isinstance(result, Exception):
            error_msg = str(result)
            logger.error("  ✗ %s: %s", name, result)

            cache_cmd = _get_cache_clear_command(name, error_msg)
            logger.error("  💡 Clear cache: %s", cache_cmd)

            results["failed"] += 1
        elif isinstance(result, dict):
            logger.info(
                "  ✓ %s: dates=%s, places=%s, groups=%s",
                name,
                result.get("dates"),
                result.get("places"),
                result.get("groups"),
            )
            results["processed"] += 1
            results["chapters"].append(name)


async def process_chapters_parallel(
    parsed_files: List[Path],
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
    max_parallel: int = 3,
) -> Dict[str, Any]:
    """Process multiple chapters in parallel."""
    results: Dict[str, Any] = {"processed": 0, "failed": 0, "chapters": []}

    # Process in batches to limit concurrency
    for i in range(0, len(parsed_files), max_parallel):
        batch = parsed_files[i : i + max_parallel]
        logger.info(f"Processing batch {i//max_parallel + 1}: {len(batch)} chapters")

        # Create tasks for this batch
        tasks = _create_chapter_tasks(batch, grok_client, output_root, config)

        # Run batch in parallel
        batch_results = await asyncio.gather(
            *[t[1] for t in tasks], return_exceptions=True
        )

        # Process results
        _process_batch_results(tasks, batch_results, results)

    return results


async def extract_events_batch_async(
    parsed_files: List[Path],
    grok_client: GrokClient,
    output_dir: Path,  # pylint: disable=unused-argument
) -> List[Path]:
    """Extract events from multiple chapters in single API call."""
    _ = output_dir  # Reserved for future use
    if not parsed_files:
        return []

    # Load all parsed data
    chapters_data = []
    for pf in parsed_files:
        with open(pf, "r", encoding="utf-8") as f:
            data = json.load(f)
            chapters_data.append({"file": pf, "data": data})

    # Batch prompt
    prompt = f"""Extract events and sub-events from these {len(chapters_data)} chapters.

For each chapter, identify the main event and break it into sub-events (specific actions/battles).

Return JSON:
{{
  "chapters": [
    {{
      "chapter_number": 1,
      "Event": {{
        "EventID": "01ABC...",
        "Event_summary": "Main event description",
        "Sub-events": [
          {{
            "Sub-eventID": "01DEF...",
            "Sub-event_summary": "Specific action description"
          }}
        ]
      }}
    }}
  ]
}}

Chapters:
"""

    for i, ch in enumerate(chapters_data, 1):
        data = ch["data"]
        para_count = len(data.get("paragraphs", []))
        prompt += f"\n{i}. Chapter {data.get('chapter_number')}: {data.get('chapter_title')} ({para_count} paragraphs)"

        # Add first few paragraphs for context
        for j, para in enumerate(data.get("paragraphs", [])[:5], 1):
            prompt += f"\n   Para {j}: {para.get('text', '')[:200]}..."

    # Single API call
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, lambda: grok_client.extract_json(prompt, cache_type="events")
    )

    # Save individual event files
    output_files = []
    chapters_response = response.get("chapters", [])

    for i, ch in enumerate(chapters_data):
        chapter_events = chapters_response[i] if i < len(chapters_response) else {}

        stem = ch["file"].stem.replace("-parsed", "")
        event_file = ch["file"].parent / f"{stem}-event.json"

        # Add metadata
        event_output = {
            "Book": ch["data"].get("book", ""),
            "Chapter": ch["data"].get("chapter_title", ""),
            "Event": chapter_events.get("Event", {}),
        }

        with open(event_file, "w", encoding="utf-8") as f:
            json.dump(event_output, f, indent=2)

        output_files.append(event_file)

    return output_files


async def extract_all_async(
    event_file: Path,
    parsed_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],  # pylint: disable=unused-argument
) -> Dict[str, Any]:
    """Extract all entities in parallel with batched API calls."""
    with open(event_file, "r") as f:
        event_data = json.load(f)

    with open(parsed_file, "r") as f:
        parsed_data = json.load(f)

    # Run all extractions in parallel
    results = await asyncio.gather(
        extract_dates_batch_async(event_data, parsed_data, grok_client, output_root),
        extract_places_batch_async(event_data, parsed_data, grok_client, output_root),
        extract_people_groups_batch_async(event_data, grok_client, output_root),
        return_exceptions=True,
    )

    return {
        "dates": (
            results[0] if not isinstance(results[0], Exception) else str(results[0])
        ),
        "places": (
            results[1] if not isinstance(results[1], Exception) else str(results[1])
        ),
        "groups": (
            results[2] if not isinstance(results[2], Exception) else str(results[2])
        ),
    }


async def extract_dates_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract dates from all sub-events in single API call."""
    dates_dir = output_root / "dates"
    dates_dir.mkdir(parents=True, exist_ok=True)

    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return 0

    # Batch prompt
    prompt = f"""Extract all dates from these {len(sub_events)} sub-events. Return JSON:
{{"dates": [{{"sub_event_id": "ID", "dates": [{{"date": "YYYY-MM-DD", "type": "exact|approximate", "precision": "day|month|year"}}]}}]}}

Sub-events:
"""
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"

    # Single API call
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None, lambda: grok_client.extract_json(prompt, cache_type="dates")
        )
    except Exception as e:
        logger.error(f"Dates batch extraction failed: {e}")
        return 0

    # Process and save
    from src.utils.file_lock import write_json_with_lock

    index_file = dates_dir / "index.json"
    index = json.load(open(index_file)) if index_file.exists() else {}

    count = 0
    for item in response.get("dates", []):
        for date_obj in item.get("dates", []):
            date_str = date_obj.get("date", "")
            if date_str and date_str not in index:
                date_file = dates_dir / f"{date_str}.json"
                write_json_with_lock(date_file, {"date": date_str, "mentions": []})
                index[date_str] = str(date_file.name)
            count += 1

    write_json_with_lock(index_file, index)
    return count


async def extract_places_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract places from all sub-events in single API call."""
    places_dir = output_root / "places"
    places_dir.mkdir(parents=True, exist_ok=True)

    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return 0

    # Batch prompt
    prompt = f"""Extract all places from these {len(sub_events)} sub-events. Return JSON:
{{"places": [{{"sub_event_id": "ID", "places": [{{"name": "Name", "type": "city|town|region", "coordinates": {{"latitude": 0, "longitude": 0}}}}]}}]}}

Sub-events:
"""
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"

    # Single API call
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, lambda: grok_client.extract_json(prompt, cache_type="places")
    )

    # Process and save
    from src.utils.file_lock import write_json_with_lock

    index_file = places_dir / "index.json"
    if index_file.exists():
        with open(index_file, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    count = 0
    for item in response.get("places", []):
        for place_obj in item.get("places", []):
            place_name = place_obj.get("name", "").lower().replace(" ", "_")
            if place_name and place_name not in index:
                place_file = places_dir / f"{place_name}.json"
                write_json_with_lock(
                    place_file, {"name": place_obj.get("name"), "mentions": []}
                )
                index[place_name] = str(place_file.name)
            count += 1

    write_json_with_lock(index_file, index)
    return count


async def extract_people_groups_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract people groups in batch."""
    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return 0

    # Batch prompt
    prompt = f"""Extract military units/groups from these {len(sub_events)} sub-events. Return JSON:
{{"groups": [{{"sub_event_id": "ID", "groups": [{{"name": "Unit Name", "type": "division|corps|army", "country": "USA|Germany"}}]}}]}}

Sub-events:
"""
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"

    # Single API call
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, lambda: grok_client.extract_json(prompt, cache_type="peoplegroups")
    )

    # Process and save
    from src.extraction.people_groups import _normalize_name
    from src.utils.file_lock import write_json_with_lock

    groups_dir = output_root / "people_groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    index_file = groups_dir / "index.json"
    if index_file.exists():
        with open(index_file, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    count = 0
    for item in response.get("groups", []):
        for group_obj in item.get("groups", []):
            group_key = _normalize_name(group_obj.get("name", ""))
            if group_key and group_key not in index:
                group_file = groups_dir / f"{group_key}.json"
                write_json_with_lock(
                    group_file, {"name": group_obj.get("name"), "mentions": []}
                )
                index[group_key] = str(group_file.name)
            count += 1

    write_json_with_lock(index_file, index)
    return count


async def extract_people_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract people in batch."""
    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return 0

    # Batch prompt
    prompt = f"""Extract people from these {len(sub_events)} sub-events. Return JSON:
{{"people": [{{"sub_event_id": "ID", "people": [{{"name": "Full Name", "rank": "Rank", "unit": "Unit", "country": "USA|Germany"}}]}}]}}

Sub-events:
"""
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"

    # Single API call
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, lambda: grok_client.extract_json(prompt, cache_type="people")
    )

    # Process and save
    from src.extraction.people import _normalize_name
    from src.utils.file_lock import write_json_with_lock

    people_dir = output_root / "people"
    people_dir.mkdir(parents=True, exist_ok=True)

    index_file = people_dir / "index.json"
    if index_file.exists():
        with open(index_file, encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    count = 0
    for item in response.get("people", []):
        for person_obj in item.get("people", []):
            person_key = _normalize_name(person_obj.get("name", ""))
            if person_key and person_key not in index:
                person_file = people_dir / f"{person_key}.json"
                write_json_with_lock(
                    person_file, {"name": person_obj.get("name"), "mentions": []}
                )
                index[person_key] = str(person_file.name)
            count += 1

    write_json_with_lock(index_file, index)
    return count
