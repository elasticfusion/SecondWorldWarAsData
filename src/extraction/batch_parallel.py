"""Batch and parallel extraction for maximum performance."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def _load_index(index_file: Path, entity_type: str) -> dict:
    """Load a JSON index file, returning empty dict on failure."""
    try:
        if index_file.exists():
            return json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load %s index, starting fresh: %s", entity_type, e)
    return {}


async def _batch_extract(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
    entity_type: str,
    cache_type: str,
    prompt_header: str,
    response_key: str,
    inner_key: str,
    make_key: Callable[[dict], str],
    make_record: Callable[[dict], dict],
) -> int:
    """Generic batch extraction: API call → process nested response → write index + files."""
    from src.utils.file_lock import write_json_with_lock

    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return 0

    # Build prompt
    prompt = prompt_header
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"

    # API call
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None, lambda: grok_client.extract_json(prompt, cache_type=cache_type)
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("%s batch extraction failed: %s", entity_type, e)
        return 0

    # Process response
    entity_dir = output_root / entity_type
    entity_dir.mkdir(parents=True, exist_ok=True)
    index_file = entity_dir / "index.json"
    index = _load_index(index_file, entity_type)

    count = 0
    for item in response.get(response_key, []):
        if not isinstance(item, dict):
            continue
        for obj in item.get(inner_key, []):
            if not isinstance(obj, dict):
                continue
            key = make_key(obj)
            if key and key not in index:
                entity_file = entity_dir / f"{key}.json"
                write_json_with_lock(entity_file, make_record(obj))
                index[key] = str(entity_file.name)
            count += 1

    write_json_with_lock(index_file, index)
    return count


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
    """Generate cache clearing command for a specific chapter across all cache types."""
    chapter_id = name.replace("-parsed.json", "")
    return (
        f'python3 -c "from diskcache import Cache; from pathlib import Path; '
        f"[Cache(str(d)).pop(k, None) "
        f"for d in Path('cache/api').iterdir() if d.is_dir() "
        f"for k in list(Cache(str(d))) "
        f"if '{chapter_id}' in str(Cache(str(d)).get(k, ''))]; "
        f"print('Cleared cache entries for {chapter_id}')\""
    )


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
                "  ✓ %s: dates=%s, places=%s, groups=%s, people=%s",
                name,
                result.get("dates"),
                result.get("places"),
                result.get("groups"),
                result.get("people"),
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
        logger.info(
            "Processing batch %d: %d chapters", i // max_parallel + 1, len(batch)
        )

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
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            event_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load event file %s: %s", event_file.name, e)
        raise

    try:
        with open(parsed_file, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load parsed file %s: %s", parsed_file.name, e)
        raise

    # Run all extractions in parallel
    results = await asyncio.gather(
        extract_dates_batch_async(event_data, parsed_data, grok_client, output_root),
        extract_places_batch_async(event_data, parsed_data, grok_client, output_root),
        extract_people_groups_batch_async(event_data, grok_client, output_root),
        extract_people_batch_async(event_data, grok_client, output_root),
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
        "people": (
            results[3] if not isinstance(results[3], Exception) else str(results[3])
        ),
    }


async def extract_dates_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract dates from all sub-events in single API call."""
    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="dates",
        cache_type="dates",
        prompt_header=f'Extract all dates from these {n} sub-events. Return JSON:\n{{"dates": [{{"sub_event_id": "ID", "dates": [{{"date": "YYYY-MM-DD", "type": "exact|approximate", "precision": "day|month|year"}}]}}]}}\n\nSub-events:',
        response_key="dates",
        inner_key="dates",
        make_key=lambda obj: obj.get("date", ""),
        make_record=lambda obj: {"date": obj.get("date", ""), "mentions": []},
    )


async def extract_places_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract places from all sub-events in single API call."""
    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="places",
        cache_type="places",
        prompt_header=f'Extract all places from these {n} sub-events. Return JSON:\n{{"places": [{{"sub_event_id": "ID", "places": [{{"name": "Name", "type": "city|town|region", "coordinates": {{"latitude": 0, "longitude": 0}}}}]}}]}}\n\nSub-events:',
        response_key="places",
        inner_key="places",
        make_key=lambda obj: obj.get("name", "").lower().replace(" ", "_"),
        make_record=lambda obj: {"name": obj.get("name"), "mentions": []},
    )


async def extract_people_groups_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract people groups in batch."""
    from src.extraction.people_groups import _normalize_name

    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="people_groups",
        cache_type="peoplegroups",
        prompt_header=f'Extract military units/groups from these {n} sub-events. Return JSON:\n{{"groups": [{{"sub_event_id": "ID", "groups": [{{"name": "Unit Name", "type": "division|corps|army", "country": "USA|Germany"}}]}}]}}\n\nSub-events:',
        response_key="groups",
        inner_key="groups",
        make_key=lambda obj: _normalize_name(obj.get("name", "")),
        make_record=lambda obj: {"name": obj.get("name"), "mentions": []},
    )


async def extract_people_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
) -> int:
    """Extract people in batch."""
    from src.extraction.people import _normalize_name

    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="people",
        cache_type="people",
        prompt_header=f'Extract people from these {n} sub-events. Return JSON:\n{{"people": [{{"sub_event_id": "ID", "people": [{{"name": "Full Name", "rank": "Rank", "unit": "Unit", "country": "USA|Germany"}}]}}]}}\n\nSub-events:',
        response_key="people",
        inner_key="people",
        make_key=lambda obj: _normalize_name(obj.get("name", "")),
        make_record=lambda obj: {"name": obj.get("name"), "mentions": []},
    )
