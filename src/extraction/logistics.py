"""Logistics extraction from event data."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid
from pydantic import BaseModel, Field

from src.grok_client import GrokClient, BatchModeCollecting, GrokTruncationError
from src.utils.yield_observer import count_nonempty_fulltext, observe_yield

logger = logging.getLogger(__name__)


# Pydantic models
class QuantityInfo(BaseModel):
    """Quantity information."""

    required: Optional[float] = None
    available: Optional[float] = None
    unit: Optional[str] = None
    shortage: Optional[float] = None
    excess: Optional[float] = None


class TemporalInfo(BaseModel):
    """Temporal information."""

    date_start: str = Field(description="ISO date")
    date_end: Optional[str] = Field(default=None, description="ISO date for ranges")
    date_type: str = Field(description="specific or range")
    DateID_start: Optional[str] = None
    DateID_end: Optional[str] = None
    DateMentionID: Optional[str] = None


class ImpactedOrganization(BaseModel):
    """Impacted organization."""

    PeopleGroupID: Optional[str] = None
    group_name: str
    impact_description: str


class ImpactedPerson(BaseModel):
    """Impacted person."""

    PersonID: Optional[str] = None
    name: str
    role: Optional[str] = None
    impact_description: str


class ImpactedPlace(BaseModel):
    """Impacted place."""

    PlaceID: Optional[str] = None
    place_name: str
    country: Optional[str] = None
    impact_description: str


class ImpactedEquipment(BaseModel):
    """Impacted equipment."""

    EquipmentID: Optional[str] = None
    common_name: str
    impact_description: str


class WeatherImpact(BaseModel):
    """Weather impact."""

    WeatherID: str
    impact_description: str
    severity: str = Field(description="critical, high, medium, low")


class EventMention(BaseModel):
    """Event mention."""

    EventMentionID: str
    EventID: str
    Sub_eventID: Optional[str] = None
    paragraph_numbers: List[int]
    context: str


class Resolution(BaseModel):
    """Resolution information."""

    resolved: bool
    resolution_date: Optional[str] = None
    resolution_description: Optional[str] = None
    resolution_method: Optional[str] = None


class Logistics(BaseModel):
    """Complete logistics output structure for validation."""

    LogisticsID: str
    logistics_type: str
    category: str
    description: str
    severity: str
    temporal: TemporalInfo
    delivery_method: Optional[str] = None
    status: str
    event_mentions: List[EventMention]
    extracted_date: str
    quantity: Optional[QuantityInfo] = None
    impacted_organizations: Optional[List[ImpactedOrganization]] = None
    impacted_people: Optional[List[ImpactedPerson]] = None
    impacted_places: Optional[List[ImpactedPlace]] = None
    impacted_equipment: Optional[List[ImpactedEquipment]] = None
    weather_impact: Optional[WeatherImpact] = None
    resolution: Optional[Resolution] = None


_VALID_TYPES = {
    "supply_shortage",
    "supply_excess",
    "delivery_delay",
    "transport_disruption",
    "capacity_constraint",
    "production_delay",
}
_VALID_CATEGORIES = {
    "ammunition",
    "fuel",
    "food",
    "medical",
    "equipment",
    "personnel",
    "general",
}
_VALID_SEVERITIES = {"critical", "high", "medium", "low"}
_VALID_STATUSES = {"unresolved", "in_progress", "resolved", "worsened"}


def _clamp_enum(value: str, valid: set, default: str) -> str:
    """Return value if valid, else default."""
    return value if value in valid else default


class LogisticsExtraction(BaseModel):
    """LLM extraction output."""

    type: str = Field(
        description="supply_shortage, supply_excess, delivery_delay, transport_disruption, planning_requirement, capacity_constraint"
    )
    category: str = Field(
        description="ammunition, fuel, food, medical, equipment, personnel, general"
    )
    description: str
    severity: str = Field(description="critical, high, medium, low")
    quantity: Optional[QuantityInfo] = None
    date_start: Optional[str] = Field(
        default=None, description="ISO date or approximate date"
    )
    date_end: Optional[str] = Field(default=None, description="ISO date for ranges")
    delivery_method: Optional[str] = Field(
        default=None,
        description="sea_transport, air_delivery, ground_transport, rail, pipeline, mixed",
    )
    status: Optional[str] = Field(
        default=None, description="unresolved, in_progress, resolved, worsened"
    )
    impacted_organizations: List[str] = Field(default_factory=list)
    impacted_people: List[str] = Field(default_factory=list)
    impacted_places: List[str] = Field(default_factory=list)
    impacted_equipment: List[str] = Field(default_factory=list)
    weather: Optional[str] = None
    resolution_resolved: Optional[bool] = None
    resolution_date: Optional[str] = None
    resolution_description: Optional[str] = None
    resolution_method: Optional[str] = None
    context: Optional[str] = None
    paragraphs: List[int] = Field(default_factory=list)


class LogisticsExtractionList(BaseModel):
    """Wrapper for list of logistics extractions."""

    logistics: List[LogisticsExtraction] = Field(default_factory=list)


def _build_entity_index(
    output_root: Path,
    entity_type: str,
    id_field: str,
    name_field: str,
    nested_path: Optional[str] = None,
) -> Dict[str, str]:
    """Build entity name -> ID index.

    ``nested_path`` (dotted, e.g. "extracted_data.description") is read when the top-level
    ``name_field`` is absent — needed for weather, whose human description is stored under
    ``extracted_data.description`` (NOT a top-level ``description``). Without it the weather index
    was ALWAYS empty, so the logistics weather-impact link never fired.
    """
    entity_dir = output_root / entity_type
    if not entity_dir.exists():
        return {}

    index: Dict[str, str] = {}
    for json_file in entity_dir.glob("*.json"):
        if json_file.name in ["index.json", "duplicate_report.json"]:
            continue
        _index_one_file(json_file, id_field, name_field, nested_path, index)

    if not index:
        logger.info(
            "OBSERV logistics.entity_index_empty entity=%s name_field=%s nested=%s",
            entity_type,
            name_field,
            nested_path or "-",
        )
    return index


def _index_one_file(json_file, id_field, name_field, nested_path, index) -> None:
    """Read one entity file and add its (name -> id) to ``index`` (fail-safe skip on error)."""
    try:
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.debug("Skipping %s: %s", json_file.name, e)
        return
    if not isinstance(
        data, dict
    ):  # a non-dict top-level file -> skip (don't abort the build)
        return
    entity_id = data.get(id_field)
    name = data.get(name_field) or (
        _nested_get(data, nested_path) if nested_path else None
    )
    if entity_id and name:
        index[name] = entity_id


def _nested_get(d, dotted):
    """Read a dotted nested key from a dict (e.g. 'extracted_data.description'); None if absent."""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _get_sub_event_text(sub_event: Dict[str, Any]) -> str:
    """Extract text from a sub-event."""
    text = sub_event.get("text", "")
    if not text and "Sub-event_fulltext" in sub_event:
        fulltext = sub_event["Sub-event_fulltext"]
        if isinstance(fulltext, dict):
            text = "\n\n".join(fulltext.values())
        else:
            text = str(fulltext)
    return text


def _batch_extract_logistics(
    sub_events: List[Dict[str, Any]],
    grok_client: GrokClient,
    chunk_size: int = 10,
) -> Dict[str, List[LogisticsExtraction]]:
    """Extract logistics from sub-events, chunked for large chapters.

    Returns dict mapping sub_event_id → list of LogisticsExtraction.
    """
    # Build sub-event texts
    relevant = []
    for se in sub_events:
        text = _get_sub_event_text(se)
        if text:
            relevant.append((se.get("Sub-eventID", ""), text))

    if not relevant:
        return {}

    # Chunk to avoid truncation on large chapters
    chunks = [relevant[i : i + chunk_size] for i in range(0, len(relevant), chunk_size)]
    all_results: Dict[str, List[LogisticsExtraction]] = {}

    from src.utils.chunked_extract import extract_with_chunk_halving

    return extract_with_chunk_halving(
        chunks, lambda chunk: _extract_logistics_chunk(chunk, grok_client), "logistics"
    )


def _extract_logistics_chunk(
    relevant: list, grok_client: GrokClient
) -> Dict[str, List[LogisticsExtraction]]:
    """Extract logistics for a chunk of sub-events."""
    sub_event_block = "\n\n".join(
        f"--- Sub-event [{seid}] ---\n{text}" for seid, text in relevant
    )

    prompt = f"""Extract logistics issues from these WWII historical sub-events.

{sub_event_block}

Look for:
1. Supply problems: shortages, delays, disruptions (fuel, ammunition, food, equipment)
2. Transportation issues: damaged roads/rails, vehicle losses, movement restrictions
3. Capacity constraints: port limits, beach capacity, force level restrictions

For each issue found, extract ALL of these fields:
- type: supply_shortage, supply_excess, delivery_delay, transport_disruption, capacity_constraint, production_delay
- category: ammunition, fuel, food, medical, equipment, personnel, general
- description: detailed description of the problem
- severity: critical, high, medium, low
- date_start, date_end: ISO dates (YYYY-MM-DD) if mentioned
- delivery_method: sea_transport, air_delivery, ground_transport, rail, pipeline, mixed (if applicable)
- status: unresolved, in_progress, resolved, worsened
- quantity: {{"required": number, "available": number, "unit": "string", "shortage": number}} (if numbers mentioned)
- impacted_organizations: list of military unit/organization names affected
- impacted_people: list of person names involved (commanders, officers)
- impacted_places: list of place names where the issue occurred or was felt (cities, ports, beaches, roads)
- impacted_equipment: list of equipment types affected
- weather: description of weather impact if relevant
- resolution_resolved: true/false if resolution mentioned
- resolution_date: ISO date of resolution
- resolution_description: how it was resolved
- resolution_method: air_delivery, sea_transport, ground_transport, reallocation, local_procurement, rationing, substitution
- context: one-sentence summary of the logistics issue in context
- paragraphs: list of paragraph numbers where this issue is mentioned

Return JSON object keyed by sub-event ID:
{{"<Sub-eventID>": {{"logistics": [<items>]}}, ...}}
Return empty logistics arrays for sub-events with no issues."""

    try:
        from src.utils.prompt_loader import render_prompt as _rp

        prompt = _rp("logistics", sub_event_block=sub_event_block)
    except (
        Exception
    ) as e:  # noqa: BLE001 - never call the API with an unrendered prompt
        # A prompt-build failure (template drift / missing var) must NOT fall through to the API
        # with the raw unrendered f-string (which yields garbage/empty that reads as "0 logistics").
        logger.error(
            "OBSERV logistics.prompt_build_failed -> returning {} (no API call): %s", e
        )
        return {}

    try:
        response = grok_client.extract_json(
            prompt=prompt,
            use_cache=True,
            cache_type="logistics",
        )
    except BatchModeCollecting:
        raise  # collect pass: propagate so the event is NOT marked processed
    except GrokTruncationError:
        # Propagate to extract_with_chunk_halving (recursive halving, #488) instead of swallowing
        # to {} (which dropped the whole chunk on truncation).
        logger.warning("OBSERV logistics.truncated -> propagating to chunk halver")
        raise
    except Exception as e:
        logger.error("Batch logistics extraction failed: %s", e)
        return {}

    return _parse_logistics_response(response)


def _parse_logistics_response(
    response: Any,
) -> Dict[str, List[LogisticsExtraction]]:
    """Parse and validate batched logistics response."""
    if not isinstance(response, dict):
        return {}
    result: Dict[str, List[LogisticsExtraction]] = {}
    for seid, data in response.items():
        # Accept BOTH shapes the LLM may return: a bare list of items, OR a
        # {"logistics": [...]} wrapper. Requiring the wrapper silently dropped bare-list
        # responses (a cause of logistics persisting 0 despite a successful batch).
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("logistics", [])
        else:
            items = []
        validated = []
        for item in items:
            parsed = _validate_logistics_item(item)
            if parsed is not None:
                validated.append(parsed)
        if validated:
            result[seid] = validated
    return result


def _coerce_logistics_item(
    item: Dict[str, Any], exc: Exception
) -> Optional[LogisticsExtraction]:
    """Best-effort: persist an item with real descriptive content by coercing enum defaults."""
    # Best-effort: an item with real descriptive content should persist even if it omits a
    # required enum field — coerce defaults (resolved specifics are enrichment, not gating).
    desc = str(item.get("description") or "").strip()
    if not desc:
        logger.debug("Skipping empty logistics item: %s", exc)
        return None
    try:
        return LogisticsExtraction.model_validate(
            {
                "type": item.get("type") or "capacity_constraint",
                "category": item.get("category") or "general",
                "description": desc,
                "severity": item.get("severity") or "medium",
                **{
                    k: v
                    for k, v in item.items()
                    if k not in ("type", "category", "description", "severity")
                },
            }
        )
    except Exception as e2:  # noqa: BLE001 - truly unusable
        logger.debug("Skipping unusable logistics item: %s", e2)
        return None


def _validate_logistics_item(item: Any) -> Optional[LogisticsExtraction]:
    """Validate one logistics item: require some descriptive content, try strict validation, then
    coerce required enum defaults rather than drop an item that has real content. Returns the
    LogisticsExtraction or None if unusable."""
    if not isinstance(item, dict):
        return None
    # Uniform floor: an empty description is not a usable logistics record.
    if not str(item.get("description") or "").strip():
        return None
    try:
        return LogisticsExtraction.model_validate(item)
    except Exception as e:
        return _coerce_logistics_item(item, e)


def _extract_logistics_with_llm(
    _event_data: Dict[str, Any],
    sub_event: Dict[str, Any],
    grok_client: GrokClient,
) -> Optional[List[LogisticsExtraction]]:
    """Extract logistics from sub-event using LLM."""
    # Get text from either 'text' field or 'Sub-event_fulltext' dictionary
    text = sub_event.get("text", "")
    if not text and "Sub-event_fulltext" in sub_event:
        # Combine all paragraphs into single text
        fulltext = sub_event["Sub-event_fulltext"]
        if isinstance(fulltext, dict):
            text = "\n\n".join(fulltext.values())
        else:
            text = str(fulltext)

    if not text:
        return None

    prompt = f"""Extract logistics issues from this WWII historical text.

Text:
{text}

Look for:
1. Supply problems: shortages, delays, disruptions (fuel, ammunition, food, equipment)
2. Transportation issues: damaged roads/rails, vehicle losses, movement restrictions
3. Capacity constraints: port limits, beach capacity, force level restrictions

For each issue found, extract:
- Type: supply_shortage, supply_excess, delivery_delay, transport_disruption, capacity_constraint, production_delay
- Category: fuel, ammunition, food, equipment, personnel, general
- Description: what the problem was
- Severity: critical, high, medium, low
- Organizations/units affected
- Dates if mentioned

Return JSON with "logistics" array. Extract ALL logistics issues mentioned, even if dates are unknown."""

    try:
        result = grok_client.extract_structured(
            prompt=prompt,
            schema=LogisticsExtractionList,  # Use wrapper model
            use_cache=True,
            cache_type="logistics",
        )
        # Extract the list from the wrapper
        if result and hasattr(result, "logistics") and result.logistics:
            logger.debug("LLM returned %d logistics issue(s)", len(result.logistics))
            return result.logistics if len(result.logistics) > 0 else None
        logger.debug("LLM returned empty logistics list")
        return None
    except Exception as e:
        logger.warning("LLM extraction failed: %s", e)
        return None


def _build_temporal(
    extraction: LogisticsExtraction, dates_index: Dict[str, Any]
) -> TemporalInfo:
    """Build temporal object. DateID resolution is interval-aware (shared
    _resolve_date_link: exact date_start OR resolved-interval overlap), against the
    dict-of-dicts index from _build_date_id_lookup."""
    from src.extraction.weather_central import _resolve_date_link

    if not extraction.date_start:
        return TemporalInfo(
            date_start="unknown",
            date_end=None,
            date_type="unknown",
            DateID_start=None,
            DateID_end=None,
        )

    date_type = "range" if extraction.date_end else "specific"
    date_id_start, _ = _resolve_date_link(extraction.date_start, dates_index)
    date_id_end = None
    if extraction.date_end:
        date_id_end, _ = _resolve_date_link(extraction.date_end, dates_index)

    return TemporalInfo(
        date_start=extraction.date_start,
        date_end=extraction.date_end,
        date_type=date_type,
        DateID_start=date_id_start,
        DateID_end=date_id_end,
    )


def _link_people(
    names: List[str], people_index: Dict[str, str]
) -> List[Dict[str, Any]]:
    """People -> PersonID by exact name (people are exact-by-design across the codebase;
    fuzzy person-matching risks wrong-person links)."""
    out = []
    for name in names or []:
        pid = people_index.get(name) or people_index.get(name.lower())
        out.append({"PersonID": pid, "name": name, "impact_description": ""})
    return out


def _link_groups(names: List[str], groups_index: Any) -> List[Dict[str, Any]]:
    """Organizations -> PeopleGroupID via the shared unit_key resolver (structural match;
    no risky substring fallback)."""
    from src.extraction.group_resolver import resolve_group_id

    out = []
    for name in names or []:
        gid = resolve_group_id(name, groups_index)
        out.append({"PeopleGroupID": gid, "group_name": name, "impact_description": ""})
    return out


def _link_places(
    names: List[str], places_index: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Places -> PlaceID via the shared alias-aware matcher."""
    from src.extraction.weather_central import _match_place_id

    out = []
    for name in names or []:
        pid = _match_place_id(name, places_index)
        out.append({"PlaceID": pid, "place_name": name, "impact_description": ""})
    return out


def _link_equipment(
    names: List[str], equipment_index: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Equipment -> EquipmentID via the shared disambiguator (canonical) + index."""
    from src.extraction.equipment_disambiguation import resolve_designation

    out = []
    for name in names or []:
        res = resolve_designation(name)
        canonical = (res or {}).get("canonical_name") or name
        eid = equipment_index.get(canonical.lower()) or equipment_index.get(
            name.lower()
        )
        out.append({"EquipmentID": eid, "common_name": name, "impact_description": ""})
    return out


def _build_weather_impact(
    weather_desc: Optional[str], severity: str, weather_index: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Build weather impact if matching weather found.

    Matches the extraction's weather text to an indexed weather record by NORMALIZED EQUALITY (not a
    bare ``desc in weather_desc`` substring — a short index description like "rain" would falsely link
    to any text containing that word, attaching the wrong WeatherID). Normalization lowercases +
    collapses whitespace so trivial formatting differences still match.
    """
    if not weather_desc:
        return None

    def _norm(s: str) -> str:
        return " ".join((s or "").lower().split())

    target = _norm(weather_desc)
    if not target:
        return None
    for desc, weather_id in weather_index.items():
        nd = _norm(desc)
        if not nd:
            continue
        # Exact normalized match, or a WHOLE-phrase containment bounded by word edges (so "rain"
        # doesn't match "terrain" / "drainage"). Require the indexed desc to be reasonably specific
        # (>=4 chars) before allowing containment, so a 1-2 char key can't over-match.
        if nd == target or (
            len(nd) >= 4
            and re.search(rf"(?<![a-z0-9]){re.escape(nd)}(?![a-z0-9])", target)
        ):
            logger.info(
                "OBSERV logistics.weather_linked weather_id=%s desc=%r",
                weather_id,
                nd[:40],
            )
            return {
                "WeatherID": weather_id,
                "impact_description": weather_desc,
                "severity": severity,
            }
    logger.info(
        "OBSERV logistics.weather_unmatched desc=%r candidates=%d",
        target[:40],
        len(weather_index),
    )
    return None


def _build_resolution(extraction: LogisticsExtraction) -> Optional[Dict[str, Any]]:
    """Build resolution data if present."""
    if extraction.resolution_resolved is None:
        return None
    return {
        "resolved": extraction.resolution_resolved,
        "resolution_date": extraction.resolution_date,
        "resolution_description": extraction.resolution_description,
        "resolution_method": extraction.resolution_method,
    }


def _build_logistics_data(
    extraction: LogisticsExtraction,
    event_data: Dict[str, Any],
    sub_event: Dict[str, Any],
    people_index: Dict[str, str],
    groups_index: Dict[str, str],
    places_index: Dict[str, str],
    equipment_index: Dict[str, str],
    weather_index: Dict[str, str],
    dates_index: Dict[str, str],
) -> Dict[str, Any]:
    """Build complete logistics data structure."""
    data = {
        "LogisticsID": str(ulid.new()),
        "logistics_type": _clamp_enum(extraction.type, _VALID_TYPES, "supply_shortage"),
        "category": _clamp_enum(extraction.category, _VALID_CATEGORIES, "general"),
        "description": extraction.description,
        "severity": _clamp_enum(extraction.severity, _VALID_SEVERITIES, "medium"),
        "temporal": _build_temporal(extraction, dates_index).model_dump(
            exclude_none=True
        ),
        "delivery_method": extraction.delivery_method,
        "status": _clamp_enum(
            extraction.status or "unresolved", _VALID_STATUSES, "unresolved"
        ),
        "event_mentions": [
            {
                "EventMentionID": str(ulid.new()),
                "EventID": event_data.get("EventID"),
                "Sub_eventID": sub_event.get("Sub-eventID"),
                "paragraph_numbers": extraction.paragraphs,
                "context": extraction.context or "",
            }
        ],
        "extracted_date": datetime.now(timezone.utc).isoformat(),
    }

    # Add optional fields
    if extraction.quantity:
        data["quantity"] = extraction.quantity.model_dump(exclude_none=True)
    if orgs := _link_groups(extraction.impacted_organizations, groups_index):
        data["impacted_organizations"] = orgs
    if people := _link_people(extraction.impacted_people, people_index):
        data["impacted_people"] = people
    if places := _link_places(extraction.impacted_places, places_index):
        data["impacted_places"] = places
    if equipment := _link_equipment(extraction.impacted_equipment, equipment_index):
        data["impacted_equipment"] = equipment
    if weather := _build_weather_impact(
        extraction.weather, extraction.severity, weather_index
    ):
        data["weather_impact"] = weather
    if resolution := _build_resolution(extraction):
        data["resolution"] = resolution

    return data


def _load_existing_keys(logistics_dir: Path) -> set:
    """Load dedup keys from existing logistics files."""
    keys = set()
    for f in logistics_dir.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            d = json.loads(f.read_text())
            em = d.get("event_mentions", [{}])[0]
            keys.add(
                (
                    em.get("Sub_eventID", ""),
                    d.get("category", ""),
                    d.get("logistics_type", ""),
                    d.get("temporal", {}).get("date_start", ""),
                )
            )
        except Exception:
            pass
    return keys


def _save_extraction(
    extraction, event, sub_event, indexes, logistics_dir, existing_keys
) -> bool:
    """Validate, dedup, and save a single logistics extraction. Returns True if saved."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    dedup_key = (
        sub_event_id,
        _clamp_enum(extraction.category, _VALID_CATEGORIES, "general"),
        _clamp_enum(extraction.type, _VALID_TYPES, "supply_shortage"),
        extraction.date_start or "unknown",
    )
    if dedup_key in existing_keys:
        return False

    try:
        people_idx, groups_idx, places_idx, equip_idx, weather_idx, dates_idx = indexes
        data = _build_logistics_data(
            extraction,
            event,
            sub_event,
            people_idx,
            groups_idx,
            places_idx,
            equip_idx,
            weather_idx,
            dates_idx,
        )
        validated = Logistics.model_validate(data)

        date_str = (
            extraction.date_start.replace("-", "")
            if extraction.date_start
            else "UNKNOWN"
        )
        # date_start is UNTRUSTED LLM free-text (category/logistics_type are enum-clamped, but
        # date_str is not) and the cosmetic .replace('-','') does NOT strip '/' or '..' — route the
        # composed name through the shared path-traversal sanitizer + symlink-containment guard
        # (mirrors dates.py/weather_central.py/casualties.py; the one date-in-filename writer the
        # Pattern-3 campaign missed).
        from src.extraction.batch_parallel import (
            _safe_entity_filename,
            _contained_entity_path,
        )

        proposed = f"{data['category']}_{data['logistics_type']}_{date_str}_{data['LogisticsID'][:8]}.json"
        filename = _safe_entity_filename(proposed, data["LogisticsID"])
        target = _contained_entity_path(logistics_dir, filename)
        if target is None:
            logger.warning(
                "OBSERV logistics.filename_contained_fallback id=%s",
                data["LogisticsID"],
            )
            filename = f"{data['LogisticsID']}.json"
            target = logistics_dir / filename
        from src.utils.file_lock import write_json_with_lock

        write_json_with_lock(target, validated.model_dump(exclude_none=True))
        # Mark deduped ONLY after a confirmed write — adding before the write poisoned the key on a
        # validate/write failure, so a later identical extraction was silently dropped (data loss).
        existing_keys.add(dedup_key)
        logger.info("Extracted: %s", filename)
        return True
    except Exception as e:
        logger.error(
            "OBSERV logistics.save_failed key=%s err=%s", dedup_key, str(e)[:160]
        )
        return False


def extract_logistics_from_event(
    event_file: Path,
    output_root: Path,
    grok_client: Optional[GrokClient] = None,
) -> Optional[Path]:
    """Extract logistics from event file."""
    if not grok_client:
        logger.warning("No Grok client provided, skipping logistics extraction")
        return None

    # Load event data
    try:
        with open(event_file, encoding="utf-8") as f:
            event_data = json.load(f)
    except Exception as e:
        logger.error("Failed to load event file: %s", e)
        return None

    # Build entity indexes via the SHARED libraries (not bespoke exact/substring matching).
    from src.extraction.group_resolver import build_group_unitkey_index
    from src.extraction.places import _build_place_name_index
    from src.extraction.weather_central import _build_date_id_lookup
    from src.utils.entity_index import build_name_index

    people_index = build_name_index(output_root / "people", "PersonID", "name")
    groups_index = build_group_unitkey_index(output_root / "people_groups")
    places_index, _ = _build_place_name_index(output_root / "places")
    equipment_index = build_name_index(
        output_root / "equipment", "EquipmentID", "common_name"
    )
    weather_index = _build_entity_index(
        output_root,
        "weather",
        "WeatherID",
        "description",
        nested_path="extracted_data.description",
    )
    dates_index = _build_date_id_lookup(output_root / "dates")

    # Create output directory
    logistics_dir = output_root / "logistics"
    logistics_dir.mkdir(parents=True, exist_ok=True)

    # Get event data - handle both old and new formats
    if "Event" in event_data:
        # New format: {"Chapter": "...", "Event": {...}}
        event = event_data["Event"]
        sub_events = event.get("Sub-events", [])
    else:
        # Old format: {"EventID": "...", "Sub-events": [...]}
        event = event_data
        sub_events = event_data.get("Sub-events", [])

    extracted_count = 0

    logger.info("Processing %d sub-events for logistics extraction", len(sub_events))

    existing_keys = _load_existing_keys(logistics_dir)

    # Batch extract from all sub-events in single API call
    batch_results = _batch_extract_logistics(sub_events, grok_client)

    indexes = (
        people_index,
        groups_index,
        places_index,
        equipment_index,
        weather_index,
        dates_index,
    )

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        extractions = batch_results.get(sub_event_id, [])
        if not extractions:
            continue

        logger.info(
            "Found %d logistics issue(s) in sub-event %s",
            len(extractions),
            sub_event_id[:8],
        )

        for extraction in extractions:
            if _save_extraction(
                extraction, event, sub_event, indexes, logistics_dir, existing_keys
            ):
                extracted_count += 1

    if extracted_count > 0:
        logger.info("Extracted %d logistics issue(s)", extracted_count)

    _observe_logistics_yield(event, event_data, sub_events, extracted_count)
    _rebuild_index(logistics_dir)
    return logistics_dir if extracted_count > 0 else None


def _observe_logistics_yield(event, event_data, sub_events, extracted_count) -> None:
    """Emit the logistics expected-yield baseline (fail-safe)."""
    observe_yield(
        "logistics",
        (event.get("EventID") if isinstance(event, dict) else "") or "?",
        len(sub_events),
        count_nonempty_fulltext(event_data),
        extracted_count,
    )


def _rebuild_index(logistics_dir: Path) -> None:
    """Build index.json mapping key → filename."""
    index: Dict[str, str] = {}
    for f in sorted(logistics_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        # Key on the FULL stem (includes the unique LogisticsID[:8] suffix). Stripping the ULID
        # (rsplit('_',1)[0]) collapsed DISTINCT sub-events that share category_type_date into one
        # index entry, so only the last survived -> the rest were unreachable via the index.
        key = f.stem
        if key in index:
            logger.warning(
                "OBSERV logistics.index_key_collision key=%s existing=%s new=%s",
                key,
                index[key],
                f.name,
            )
        index[key] = f.name
    with open(logistics_dir / "index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
