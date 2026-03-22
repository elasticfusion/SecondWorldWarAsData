"""Batch and parallel extraction for maximum performance."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import ulid as ulid_mod

from src.grok_client import GrokClient, current_book
from src.utils.file_lock import write_json_with_lock
from src.utils.json_validator import _fix_invalid_ulids

logger = logging.getLogger(__name__)


def _load_index(index_file: Path, entity_type: str) -> dict:
    """Load a JSON index file, returning empty dict on failure."""
    try:
        if index_file.exists():
            return json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load %s index, starting fresh: %s", entity_type, e)
    return {}


def _make_date_key(obj: dict) -> str:
    """Create date lookup key, including time if present. Rejects non-WWII dates."""
    key = obj.get("date_start") or obj.get("date", "")
    if not key:
        return ""
    # Extract year — reject dates outside 1919-1955 (WWII era + context)
    year_match = re.search(r"\d{4}", key)
    if year_match:
        year = int(year_match.group())
        if year < 1919 or year > 1955:
            return ""
    time_start = obj.get("time_start")
    if time_start:
        return f"{key}T{time_start}"
    return key


def _make_date_record(obj: dict) -> dict:
    """Create spec-compliant date record from LLM output."""
    return {
        "date_start": obj.get("date_start") or obj.get("date", ""),
        "date_end": obj.get("date_end"),
        "time_start": obj.get("time_start"),
        "time_end": obj.get("time_end"),
        "time_precision": obj.get("time_precision"),
        "date_precision": obj.get("date_precision"),
        "time_source": obj.get("time_source"),
        "original_text": obj.get("original_text", ""),
        "normalized_datetime": None,
    }


def _make_date_filename(key: str, entity_id: str) -> str:
    """Create spec-compliant date filename: YYYYMMDD[_HHMM]_ULID8.json"""
    date_part = key.split("T")[0] if "T" in key else key
    time_part = key.split("T")[1] if "T" in key else None

    safe = (
        date_part.replace("-", "")
        .replace("early", "E")
        .replace("mid", "M")
        .replace("late", "L")
        .replace("spring", "SP")
        .replace("summer", "SU")
        .replace("fall", "FA")
        .replace("autumn", "AU")
        .replace("winter", "WI")
    )
    if time_part:
        safe += f"_{time_part.replace(':', '')}"
    return f"{safe}_{entity_id[:8]}.json"


def _get_or_create_entity(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    key: str,
    obj: dict,
    index: dict,
    entity_dir: Path,
    make_record: Callable[[dict], dict],
    id_field: str,
    make_filename: Optional[Callable[[str, str], str]] = None,
) -> tuple:
    """Get existing or create new entity file. Returns (entity_file, entity_id, record)."""
    if key not in index:
        entity_id = str(ulid_mod.new())
        record = make_record(obj)
        if id_field:
            record[id_field] = entity_id
            record["event_mentions"] = []
        filename = make_filename(key, entity_id) if make_filename else f"{key}.json"
        entity_file = entity_dir / filename
        write_json_with_lock(entity_file, record)
        index[key] = str(entity_file.name)
        return entity_file, entity_id, record

    entity_file = entity_dir / index[key]
    try:
        with open(entity_file, "r", encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, json.JSONDecodeError):
        record = {}
    return entity_file, record.get(id_field, ""), record


def _add_event_mention_batch(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    record: dict,
    entity_file: Path,
    seid: str,
    se_name: str,
    event_id: str,
    event_name: str,
    meta: Dict[str, str],
    source_obj: Optional[dict] = None,
) -> None:
    """Add event_mention to entity record if not already present."""
    mentions = record.get("event_mentions", [])
    if any(m.get("Sub_eventID") == seid for m in mentions):
        return
    mention = {
        "MentionID": str(ulid_mod.new()),
        "Event_Name": event_name,
        "EventID": event_id,
        "Sub_event_Name": se_name,
        "Sub_eventID": seid,
        "book": meta.get("book", ""),
        "author": meta.get("author", ""),
        "series": meta.get("series", ""),
    }
    if source_obj:
        mention["context"] = source_obj.get("context")
        mention["original_text"] = source_obj.get("original_text", "")
        if source_obj.get("position_at_event"):
            mention["position_at_event"] = source_obj["position_at_event"]
        if source_obj.get("life_event"):
            mention["life_event"] = source_obj["life_event"]
        if source_obj.get("date_context"):
            mention["date_context"] = source_obj["date_context"]
        if source_obj.get("role_in_event"):
            mention["role_in_event"] = source_obj["role_in_event"]
    mentions.append(mention)
    record["event_mentions"] = mentions
    write_json_with_lock(entity_file, record)


def _process_entity_obj(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    obj: dict,
    make_key: Callable[[dict], str],
    make_record: Callable[[dict], dict],
    entity_dir: Path,
    index: dict,
    id_field: str,
    seid: str,
    se_name: str,
    event_id: str,
    event_name: str,
    meta: dict,
    sub_event_key: str,
    links: Dict[str, list],
    make_filename: Optional[Callable[[str, str], str]] = None,
) -> bool:
    """Process a single entity object. Returns True if processed."""
    key = make_key(obj)
    if not key:
        return False

    entity_file, entity_id, record = _get_or_create_entity(
        key, obj, index, entity_dir, make_record, id_field, make_filename
    )

    if id_field and seid and event_id:
        _add_event_mention_batch(
            record,
            entity_file,
            seid,
            se_name,
            event_id,
            event_name,
            meta,
            source_obj=obj,
        )

    if sub_event_key and seid and entity_id:
        links.setdefault(seid, []).append(entity_id)

    return True


def _process_batch_response(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    response: dict,
    response_key: str,
    inner_key: str,
    make_key: Callable[[dict], str],
    make_record: Callable[[dict], dict],
    entity_dir: Path,
    index: dict,
    id_field: str,
    se_by_id: dict,
    event_id: str,
    event_name: str,
    meta: dict,
    sub_event_key: str,
    make_filename: Optional[Callable[[str, str], str]] = None,
) -> Dict[str, Any]:
    """Process API response: create entities, add mentions, collect links."""
    count = 0
    links: Dict[str, list] = {}

    for item in response.get(response_key, []):
        if not isinstance(item, dict):
            continue
        seid = item.get("sub_event_id", "")
        se_name = se_by_id.get(seid, {}).get("Sub-event_summary", "")

        for obj in item.get(inner_key, []):
            if isinstance(obj, dict) and _process_entity_obj(
                obj,
                make_key,
                make_record,
                entity_dir,
                index,
                id_field,
                seid,
                se_name,
                event_id,
                event_name,
                meta,
                sub_event_key,
                links,
                make_filename,
            ):
                count += 1

    return {"count": count, "links": links}


async def _batch_extract(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
    id_field: str = "",
    sub_event_key: str = "",
    book_meta: Optional[Dict[str, str]] = None,
    include_fulltext: bool = False,
    make_filename: Optional[Callable[[str, str], str]] = None,
    post_process: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Generic batch extraction with cross-referencing.

    Returns dict with 'count' and 'links' (sub_event_id → [entity_ulid]).
    """
    empty: Dict[str, Any] = {"count": 0, "links": {}}
    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    if not sub_events:
        return empty

    event_id = event_data.get("Event", {}).get("EventID", "")
    event_name = event_data.get("Chapter", "")
    meta = book_meta or {}
    se_by_id = {se.get("Sub-eventID", ""): se for se in sub_events}

    # Build prompt
    prompt = prompt_header
    for i, se in enumerate(sub_events, 1):
        prompt += f"\n{i}. [{se.get('Sub-eventID')}] {se.get('Sub-event_summary', '')}"
        if include_fulltext:
            ft = se.get("Sub-event_fulltext", {})
            for pk in sorted(ft.keys()):
                prompt += f"\n   {ft[pk]}"

    # API call
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None, lambda: grok_client.extract_json(prompt, cache_type=cache_type)
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("%s batch extraction failed: %s", entity_type, e)
        return empty

    response = _fix_invalid_ulids(response)

    # Process response
    entity_dir = output_root / entity_type
    entity_dir.mkdir(parents=True, exist_ok=True)
    index_file = entity_dir / "index.json"
    index = _load_index(index_file, entity_type)

    result = _process_batch_response(
        response,
        response_key,
        inner_key,
        make_key,
        make_record,
        entity_dir,
        index,
        id_field,
        se_by_id,
        event_id,
        event_name,
        meta,
        sub_event_key,
        make_filename,
    )

    write_json_with_lock(index_file, index)
    if post_process:
        post_process(response, entity_dir, index)
    return result


async def process_chapter_async(
    parsed_file: Path,
    event_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Process single chapter: events + entities in parallel."""
    # Set book context for per-book cache routing
    book_name = parsed_file.parent.name
    if book_name == output_root.name:
        book_name = None  # Files directly in output root — no book
    current_book.set(book_name)

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
        tasks.append((pf.parent.name, pf.name, task))

    return tasks


def _get_cache_clear_command(book_name: str) -> str:
    """Generate command to clear a book's event cache."""
    return f"rm -rf cache/api/books/{book_name}/events/"


def _process_batch_results(
    tasks: List[tuple], batch_results: list, results: Dict[str, Any]
) -> None:
    """Process results from a batch of chapters."""
    for (book_name, name, _), result in zip(tasks, batch_results):
        if isinstance(result, Exception):
            error_msg = str(result)
            logger.error("  ✗ %s: %s", name, result)

            cache_cmd = _get_cache_clear_command(book_name)
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
    heartbeat=None,
) -> Dict[str, Any]:
    """Process multiple chapters in parallel."""
    results: Dict[str, Any] = {"processed": 0, "failed": 0, "chapters": []}

    # Process in batches to limit concurrency
    for i in range(0, len(parsed_files), max_parallel):
        batch = parsed_files[i : i + max_parallel]
        batch_num = i // max_parallel + 1
        logger.info("Processing batch %d: %d chapters", batch_num, len(batch))
        if heartbeat:
            heartbeat.ping(f"Step 1 batch {batch_num}: {len(batch)} chapters")

        # Create tasks for this batch
        tasks = _create_chapter_tasks(batch, grok_client, output_root, config)

        # Run batch in parallel
        batch_results = await asyncio.gather(
            *[t[2] for t in tasks], return_exceptions=True
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
    """Extract all entities in parallel with cross-referencing."""
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

    book_meta = {
        "book": parsed_data.get("book", ""),
        "author": parsed_data.get("author", ""),
        "series": parsed_data.get("series", ""),
    }

    # Run all extractions in parallel
    results = await asyncio.gather(
        extract_dates_batch_async(
            event_data, parsed_data, grok_client, output_root, book_meta
        ),
        extract_places_batch_async(
            event_data, parsed_data, grok_client, output_root, book_meta
        ),
        extract_people_groups_batch_async(
            event_data, grok_client, output_root, book_meta
        ),
        extract_people_batch_async(event_data, grok_client, output_root, book_meta),
        return_exceptions=True,
    )

    # Write entity ULIDs into sub-events and save event file
    key_map = {0: "dates", 1: "places", 2: "peoplegroups", 3: "people"}
    sub_events = event_data.get("Event", {}).get("Sub-events", [])
    for se in sub_events:
        seid = se.get("Sub-eventID", "")
        for idx, se_key in key_map.items():
            res = results[idx]
            if isinstance(res, dict):
                new_ids = res.get("links", {}).get(seid, [])
                existing = set(se.get(se_key, []))
                se[se_key] = list(existing | set(new_ids))

    write_json_with_lock(event_file, event_data)

    def _count(res):
        if isinstance(res, dict):
            return res.get("count", 0)
        return str(res)

    return {
        "dates": _count(results[0]),
        "places": _count(results[1]),
        "groups": _count(results[2]),
        "people": _count(results[3]),
    }


async def extract_dates_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
    book_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Extract dates from all sub-events in single API call."""
    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="dates",
        cache_type="dates",
        prompt_header=(
            f"Extract all dates and times from these {n} sub-events. Return JSON:\n"
            '{"dates": [{"sub_event_id": "ID", "dates": ['
            '{"date_start": "YYYY-MM-DD", "date_end": null, '
            '"time_start": "HH:MM or null", "time_end": null, '
            '"time_precision": "exact|approximate|null", '
            '"date_precision": "exact|early|mid|late|spring|summer|fall|winter", '
            '"time_source": "German|Allied|Zulu|Local|null", '
            '"original_text": "exact quote from text"}'
            "]}]}\n"
            "For approximate dates use prefix: mid-1944-07, early-1944, summer-1942\n"
            "Sub-events:"
        ),
        response_key="dates",
        inner_key="dates",
        make_key=_make_date_key,
        make_record=_make_date_record,
        id_field="DateID",
        sub_event_key="dates",
        book_meta=book_meta,
        include_fulltext=True,
        make_filename=_make_date_filename,
    )


_VALID_GEO_TYPES = frozenset(
    [
        "city",
        "town",
        "village",
        "country",
        "region",
        "province",
        "state",
        "sea",
        "ocean",
        "river",
        "lake",
        "mountain",
        "island",
        "peninsula",
        "continent",
        "military_base",
        "battlefield",
        "fortification",
        "bridge",
        "port",
        "airfield",
        "other",
    ]
)


def _make_place_record(obj: dict) -> dict:
    """Build initial place file record from extracted data."""
    from src.extraction.places import _calculate_bounding_box, _generate_map_urls

    geo_type = obj.get("type", "other")
    if geo_type not in _VALID_GEO_TYPES:
        geo_type = "other"
    record: Dict[str, Any] = {
        "current_name": obj.get("name", ""),
        "historical_names": [],
        "aliases": [],
        "source_language": "English",
        "geography_type": geo_type,
    }
    coords = obj.get("coordinates", {})
    lat = coords.get("latitude", 0)
    lon = coords.get("longitude", 0)
    if lat and lon:
        record["coordinates"] = {
            "latitude": lat,
            "longitude": lon,
            "precision": "approximate",
            "confidence": 0.8,
        }
        record["bounding_box_100km"] = _calculate_bounding_box(lat, lon)
        record["map_urls"] = _generate_map_urls(lat, lon)
    return record


_VALID_RELATIONSHIPS = frozenset(
    ["contains", "part_of", "near", "connected_by_route", "same_as"]
)


def _process_place_relationships(response, entity_dir, index):
    """Write related_places from LLM response into place files."""
    for item in response.get("places", []):
        if not isinstance(item, dict):
            continue
        for rel in item.get("relationships", []):
            if not isinstance(rel, dict):
                continue
            rel_type = rel.get("type", "")
            if rel_type not in _VALID_RELATIONSHIPS:
                continue
            from_key = rel.get("from", "").lower().replace(" ", "_")
            to_key = rel.get("to", "").lower().replace(" ", "_")
            if not from_key or not to_key:
                continue
            from_file = index.get(from_key)
            to_file = index.get(to_key)
            if not from_file or not to_file:
                continue
            _add_relationship(entity_dir / from_file, entity_dir / to_file, rel_type)


def _add_relationship(from_path, to_path, rel_type):
    """Add a single relationship to a place file if not duplicate."""
    try:
        data = json.loads(from_path.read_text(encoding="utf-8"))
        to_data = json.loads(to_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    to_id = to_data.get("PlaceID", "")
    if not to_id:
        return
    related = data.get("related_places", [])
    if any(r.get("PlaceID") == to_id for r in related):
        return
    related.append({"PlaceID": to_id, "relationship": rel_type})
    data["related_places"] = related
    write_json_with_lock(from_path, data)


async def extract_places_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],  # pylint: disable=unused-argument
    grok_client: GrokClient,
    output_root: Path,
    book_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Extract places from all sub-events in single API call."""
    n = len(event_data.get("Event", {}).get("Sub-events", []))
    geo_types = "city|town|village|country|region|province|state|sea|ocean|river|lake|mountain|island|peninsula|continent|military_base|battlefield|fortification|bridge|port|airfield|other"
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="places",
        cache_type="places",
        prompt_header=f'Extract all places from these {n} sub-events. Return JSON:\n{{"places": [{{"sub_event_id": "ID", "places": [{{"name": "Name", "type": "{geo_types}", "coordinates": {{"latitude": 0, "longitude": 0}}, "date_context": "YYYY-MM-DD or null", "role_in_event": "role or null", "original_text": "exact quote"}}], "relationships": [{{"from": "Place A", "to": "Place B", "type": "part_of|near|contains|connected_by_route"}}]}}]}}\n\nSub-events:',
        response_key="places",
        inner_key="places",
        make_key=lambda obj: obj.get("name", "").lower().replace(" ", "_"),
        make_record=lambda obj: _make_place_record(obj),
        id_field="PlaceID",
        sub_event_key="places",
        book_meta=book_meta,
        include_fulltext=True,
        post_process=_process_place_relationships,
    )


async def extract_people_groups_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
    book_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Extract people groups in batch."""
    from src.extraction.people_groups import _normalize_name

    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="people_groups",
        cache_type="peoplegroups",
        prompt_header=f'Extract military units/groups from these {n} sub-events. Return JSON:\n{{"groups": [{{"sub_event_id": "ID", "groups": [{{"name": "Unit Name", "type": "division|corps|army", "country": "USA|Germany", "context": "role in this sub-event", "original_text": "exact quote mentioning unit"}}]}}]}}\n\nSub-events:',
        response_key="groups",
        inner_key="groups",
        make_key=lambda obj: _normalize_name(obj.get("name", "")),
        make_record=lambda obj: {
            "name": obj.get("name"),
            "group_name": obj.get("name"),
            "group_type": "military_unit",
            "source_language": "English",
        },
        id_field="GroupID",
        sub_event_key="peoplegroups",
        book_meta=book_meta,
        include_fulltext=True,
    )


async def extract_people_batch_async(
    event_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
    book_meta: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Extract people in batch — includes fulltext for complete coverage."""
    from src.extraction.people import _normalize_name

    n = len(event_data.get("Event", {}).get("Sub-events", []))
    return await _batch_extract(
        event_data,
        grok_client,
        output_root,
        entity_type="people",
        cache_type="people",
        prompt_header=(
            f"Extract ALL people mentioned in these {n} sub-events. "
            "Include every named individual, even if mentioned only once.\n"
            "For each person extract:\n"
            "- name: Full name as written\n"
            "- rank: Military rank at time of mention\n"
            "- unit: Military unit\n"
            "- country: USA, Germany, Britain, etc.\n"
            "- position_at_event: Role/position during this event\n"
            "- life_event: What happened to this person in this sub-event\n"
            "- original_text: The sentence(s) mentioning this person\n"
            'Return JSON:\n{"people": [{"sub_event_id": "ID", '
            '"people": [{"name": "Full Name", "rank": "Rank", '
            '"unit": "Unit", "country": "USA", '
            '"position_at_event": "Commander of...", '
            '"life_event": "Ordered the attack on...", '
            '"original_text": "General X ordered..."}]}]}\n\nSub-events:'
        ),
        response_key="people",
        inner_key="people",
        make_key=lambda obj: _normalize_name(obj.get("name", "")),
        make_record=lambda obj: {
            "name": obj.get("name"),
            "source_language": "English",
        },
        id_field="PersonID",
        sub_event_key="people",
        book_meta=book_meta,
        include_fulltext=True,
    )
