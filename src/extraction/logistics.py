"""Logistics extraction from event data."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid
from pydantic import BaseModel, Field

from src.grok_client import GrokClient

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

    PeopleGroupID: str
    group_name: str
    impact_description: str


class ImpactedPerson(BaseModel):
    """Impacted person."""

    PersonID: str
    name: str
    role: Optional[str] = None
    impact_description: str


class ImpactedPlace(BaseModel):
    """Impacted place."""

    PlaceID: str
    place_name: str
    country: Optional[str] = None
    impact_description: str


class ImpactedEquipment(BaseModel):
    """Impacted equipment."""

    EquipmentID: str
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
    output_root: Path, entity_type: str, id_field: str, name_field: str
) -> Dict[str, str]:
    """Build entity name -> ID index."""
    entity_dir = output_root / entity_type
    if not entity_dir.exists():
        return {}

    index = {}
    for json_file in entity_dir.glob("*.json"):
        if json_file.name in ["index.json", "duplicate_report.json"]:
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                entity_id = data.get(id_field)
                name = data.get(name_field)
                if entity_id and name:
                    index[name] = entity_id
        except Exception as e:
            logger.debug("Skipping %s: %s", json_file.name, e)

    return index


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
) -> Dict[str, List[LogisticsExtraction]]:
    """Extract logistics from all sub-events in a single API call.

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

    sub_event_block = "\n\n".join(
        f"--- Sub-event [{seid}] ---\n{text}" for seid, text in relevant
    )

    prompt = f"""Extract logistics issues from these WWII historical sub-events.

{sub_event_block}

Look for:
1. Supply problems: shortages, delays, disruptions (fuel, ammunition, food, equipment)
2. Transportation issues: damaged roads/rails, vehicle losses, movement restrictions
3. Capacity constraints: port limits, beach capacity, force level restrictions

For each issue found, extract:
- type: supply_shortage, delivery_delay, transport_disruption, capacity_constraint
- category: fuel, ammunition, food, equipment, personnel, general
- description: what the problem was
- severity: critical, high, medium, low
- impacted_organizations, impacted_people, impacted_places, impacted_equipment
- date_start, date_end (ISO dates if mentioned)

Return JSON object keyed by sub-event ID:
{{"<Sub-eventID>": {{"logistics": [<items>]}}, ...}}
Return empty logistics arrays for sub-events with no issues."""

    try:
        response = grok_client.extract_json(
            prompt=prompt,
            use_cache=True,
            cache_type="logistics",
        )
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
        items = data.get("logistics", []) if isinstance(data, dict) else []
        validated = []
        for item in items:
            try:
                validated.append(LogisticsExtraction.model_validate(item))
            except Exception as e:
                logger.debug("Skipping invalid logistics item: %s", e)
        if validated:
            result[seid] = validated
    return result


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
- Type: supply_shortage, delivery_delay, transport_disruption, capacity_constraint
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
    extraction: LogisticsExtraction, dates_index: Dict[str, str]
) -> TemporalInfo:
    """Build temporal object."""
    # Handle None dates
    if not extraction.date_start:
        return TemporalInfo(
            date_start="unknown",
            date_end=None,
            date_type="unknown",
            DateID_start=None,
            DateID_end=None,
        )

    date_type = "range" if extraction.date_end else "specific"
    date_id_start = dates_index.get(extraction.date_start)
    date_id_end = dates_index.get(extraction.date_end) if extraction.date_end else None

    return TemporalInfo(
        date_start=extraction.date_start,
        date_end=extraction.date_end,
        date_type=date_type,
        DateID_start=date_id_start,
        DateID_end=date_id_end,
    )


def _link_entities(
    names: List[str], index: Dict[str, str], id_key: str, name_key: str
) -> List[Dict[str, Any]]:
    """Link entity names to IDs from index."""
    return [
        {id_key: entity_id, name_key: name, "impact_description": ""}
        for name in names
        if (entity_id := index.get(name))
    ]


def _build_weather_impact(
    weather_desc: Optional[str], severity: str, weather_index: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Build weather impact if matching weather found."""
    if not weather_desc:
        return None
    for desc, weather_id in weather_index.items():
        if desc in weather_desc:
            return {
                "WeatherID": weather_id,
                "impact_description": weather_desc,
                "severity": severity,
            }
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
        "logistics_type": extraction.type,
        "category": extraction.category,
        "description": extraction.description,
        "severity": extraction.severity,
        "temporal": _build_temporal(extraction, dates_index).model_dump(
            exclude_none=True
        ),
        "delivery_method": extraction.delivery_method,
        "status": extraction.status or "unresolved",
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
    if orgs := _link_entities(
        extraction.impacted_organizations, groups_index, "PeopleGroupID", "group_name"
    ):
        data["impacted_organizations"] = orgs
    if people := _link_entities(
        extraction.impacted_people, people_index, "PersonID", "name"
    ):
        data["impacted_people"] = people
    if places := _link_entities(
        extraction.impacted_places, places_index, "PlaceID", "place_name"
    ):
        data["impacted_places"] = places
    if equipment := _link_entities(
        extraction.impacted_equipment, equipment_index, "EquipmentID", "common_name"
    ):
        data["impacted_equipment"] = equipment
    if weather := _build_weather_impact(
        extraction.weather, extraction.severity, weather_index
    ):
        data["weather_impact"] = weather
    if resolution := _build_resolution(extraction):
        data["resolution"] = resolution

    return data


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

    # Build entity indexes
    people_index = _build_entity_index(output_root, "people", "PersonID", "name")
    groups_index = _build_entity_index(
        output_root, "people_groups", "PeopleGroupID", "group_name"
    )
    places_index = _build_entity_index(output_root, "places", "PlaceID", "current_name")
    equipment_index = _build_entity_index(
        output_root, "equipment", "EquipmentID", "common_name"
    )
    weather_index = _build_entity_index(
        output_root, "weather", "WeatherID", "description"
    )
    dates_index = _build_entity_index(output_root, "dates", "DateID", "date_start")

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

    # Batch extract from all sub-events in single API call
    batch_results = _batch_extract_logistics(sub_events, grok_client)

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
            try:
                logistics_data = _build_logistics_data(
                    extraction,
                    event,  # Use event instead of event_data
                    sub_event,
                    people_index,
                    groups_index,
                    places_index,
                    equipment_index,
                    weather_index,
                    dates_index,
                )

                # Generate filename
                date_str = (
                    extraction.date_start.replace("-", "")
                    if extraction.date_start
                    else "UNKNOWN"
                )
                logistics_id = logistics_data["LogisticsID"][:8]
                safe_cat = extraction.category.replace("/", "_").replace("\\", "_")
                filename = (
                    f"{safe_cat}_{extraction.type}_{date_str}_{logistics_id}.json"
                )
                output_file = logistics_dir / filename

                # Validate structure
                validated = Logistics.model_validate(logistics_data)

                # Save
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(validated.model_dump(exclude_none=True), f, indent=2)

                extracted_count += 1
                logger.info("Extracted: %s", filename)

            except Exception as e:
                logger.error("Failed to process extraction: %s", e)
                continue

    if extracted_count > 0:
        logger.info("Extracted %d logistics issue(s)", extracted_count)
        return logistics_dir

    return None
