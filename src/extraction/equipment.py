"""Military equipment extraction from event data."""

import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ulid
from pydantic import BaseModel, Field, field_validator

from src.grok_client import GrokClient
from src.utils.http_pool import get_session
from src.utils.json_validator import _fix_invalid_ulids

logger = logging.getLogger(__name__)

# This module targets schema 2.24.
SCHEMA_TARGET = "2.24"


# Pydantic models for structured extraction
class UsingUnit(BaseModel):
    """Unit using equipment."""

    PeopleGroupID: str = Field(description="26-character ULID")
    name: str = Field(description="Unit name")


class UsingPerson(BaseModel):
    """Person using equipment."""

    PersonID: str = Field(description="26-character ULID")
    name: str = Field(description="Person name")


class SupportingUnit(BaseModel):
    """Supporting unit with equipment."""

    support_type: str = Field(
        description="Type: armor, naval, aircraft, artillery, etc."
    )
    PeopleGroupID: Optional[str] = Field(default=None, description="26-character ULID")
    unit_name: Optional[str] = Field(default=None, description="Unit name")
    EquipmentID: Optional[str] = Field(default=None, description="Equipment ULID")
    equipment_name: Optional[str] = Field(default=None, description="Equipment name")


class SupportingUnitInput(BaseModel):
    """LLM-provided supporting unit (resolved to IDs by _link_supporting_units)."""

    unit_name: Optional[str] = Field(default=None, description="Supporting unit name")
    support_type: Optional[str] = Field(
        default=None,
        description="The supporting unit's OWN arm (aircraft/artillery/armor/…)",
    )
    equipment_name: Optional[str] = Field(
        default=None, description="Equipment the supporting unit used (if named)"
    )


class EnvironmentalPerformanceInput(BaseModel):
    """How this equipment performed under a stated environmental/weather CONDITION, as the
    SOURCE narrates it (e.g. 'the M4 performed badly in sub-zero temperatures'). This is
    equipment-performance conditioned on weather/terrain — distinct from the ambient Weather
    entity (which records the event's weather state). Narrative-sourced; original_text
    mandatory for traceability."""

    condition: Optional[str] = Field(
        default=None,
        description="The condition (e.g. 'sub-zero temperatures', 'snow', 'bocage', 'mud')",
    )
    effect: Optional[str] = Field(
        default=None,
        description="The effect on performance (e.g. 'performed badly', 'track wear', 'engine overheating')",
    )
    original_text: Optional[str] = Field(
        default=None, description="Verbatim passage (REQUIRED for traceability)"
    )


class CrewAccountInput(BaseModel):
    """A crew member's firsthand account of operating this equipment, as the SOURCE
    narrates it. Narrative-sourced only — source tracking is mandatory: original_text
    (verbatim) + book identify the origin. PersonID is resolved from person_name."""

    person_name: Optional[str] = Field(default=None, description="Named crew member")
    role: Optional[str] = Field(
        default=None, description="e.g. commander, driver, gunner"
    )
    observations: Optional[str] = Field(
        default=None, description="What they reported about the equipment"
    )
    original_text: Optional[str] = Field(
        default=None, description="Verbatim passage (REQUIRED for traceability)"
    )
    book: Optional[str] = Field(default=None, description="Source book/title")


class RelatedEquipmentInput(BaseModel):
    """A relationship the SOURCE draws between this equipment and ANOTHER DISTINCT piece
    (its own record), e.g. predecessor/successor, or a distinct related configuration.
    Narrative-sourced only — original_text is required for traceability. Inline
    sub-designations (M4A1, 'up-gunned M4') are NOT related_equipment; they are variants.
    """

    relationship: str = Field(description="predecessor | successor | variant")
    name: str = Field(
        description="Name of the DISTINCT related equipment (e.g. 'M26 Pershing')"
    )
    basis: Optional[str] = Field(
        default=None,
        description="Short/verbatim reason the source relates them (e.g. '76mm gun vs "
        "the standard 75mm'). Null if the source just names the relation.",
    )
    original_text: Optional[str] = Field(
        default=None, description="Verbatim passage asserting the relationship"
    )


class PerformanceNotes(BaseModel):
    """Performance observations."""

    successes: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    field_modifications: List[str] = Field(default_factory=list)


class MediaItem(BaseModel):
    """Media item (photo, video, audio, document)."""

    media_type: str = Field(description="photo, video, audio, document")
    url: str = Field(description="URL to media")
    title: Optional[str] = Field(default=None, description="Media title/caption")
    source: str = Field(description="wikipedia, commons, archive, etc.")
    license: Optional[str] = Field(default=None, description="License info")
    description: Optional[str] = Field(default=None, description="Media description")
    image_scope: str = Field(
        default="representative",
        description=(
            "representative = a generic/stock image illustrating the equipment TYPE "
            "(the DEFAULT — books routinely use stock photos; do NOT claim it depicts "
            "this event). documentary = the source EXPLICITLY asserts the image is of "
            "this specific event/engagement. Vision identifies the TYPE, never the event."
        ),
    )


class EquipmentMention(BaseModel):
    """Equipment mention in event."""

    MentionID: str = Field(description="26-character ULID")
    book: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    chapter: Optional[str] = None
    paragraph_numbers: List[int] = Field(default_factory=list)
    variant_mentioned: Optional[str] = None
    context: Optional[str] = None
    original_text: Optional[str] = None
    operating_country: Optional[str] = Field(
        default=None, description="Who used it in this mention (per-mention operator)"
    )
    captured: bool = Field(
        default=False, description="Captured and used against its origin (per-mention)"
    )
    quantity: Optional[int] = Field(
        default=None, description="Exact count stated for this mention (per-mention)"
    )
    quantity_text: Optional[str] = Field(
        default=None, description="Verbatim count phrase (e.g. 'several') (per-mention)"
    )
    PlaceID: Optional[str] = Field(
        default=None, description="Linked place for this mention (denormalized)"
    )
    place_name: Optional[str] = Field(
        default=None, description="Place name for this mention (per-mention)"
    )
    assertion_source: Optional[str] = Field(
        default=None, description="narrative | media_narration (how presence asserted)"
    )
    EventID: str = Field(description="Links to Event.EventID")
    Event_Name: Optional[str] = None
    Sub_eventID: str = Field(description="Links to Sub-eventID in Event.Sub-events[]")
    Sub_event_Name: Optional[str] = None
    date: Optional[str] = None
    DateID: Optional[str] = Field(default=None, description="Links to date file")
    DateMentionID: Optional[str] = Field(
        default=None, description="Links to mention in date file"
    )
    using_unit: Optional[UsingUnit] = None
    using_person: Optional[UsingPerson] = None
    supporting_units: List[SupportingUnit] = Field(default_factory=list)
    performance_notes: Optional[PerformanceNotes] = None
    media: List[MediaItem] = Field(
        default_factory=list, description="Photos, videos, documents"
    )


class Variant(BaseModel):
    """Equipment variant — managed INLINE in the same record file. May carry its own
    specifications/images (M4A1 vs M4A3E8 differ)."""

    variant_name: str
    differences: Optional[str] = None
    alternate_names: List[str] = Field(default_factory=list)
    specifications: Optional[Dict[str, Any]] = Field(
        default=None, description="This variant's own specs (if they differ)"
    )
    images: List[Dict[str, Any]] = Field(
        default_factory=list, description="This variant's own images"
    )


class ExternalDataPoint(BaseModel):
    """Single data point from external source."""

    field: str
    value: str
    verified: bool = False
    notes: Optional[str] = None


class ExternalSource(BaseModel):
    """External data source."""

    source_type: str = Field(description="museum, archive, reference, etc.")
    source_name: str
    url: str
    data_points: List[ExternalDataPoint] = Field(default_factory=list)


class ExternalData(BaseModel):
    """External supplemental data."""

    grokipedia_url: Optional[str] = None
    wikipedia_url: Optional[str] = None
    additional_sources: List[ExternalSource] = Field(default_factory=list)


class EquipmentExtraction(BaseModel):
    """LLM extraction output."""

    common_name: str = Field(description="Common name (e.g., 'Sherman', 'Tiger')")
    technical_identifier: Optional[str] = Field(
        default=None,
        description="Official designation (e.g., 'M4', 'Panzerkampfwagen VI')",
    )
    description: Optional[str] = Field(default=None, description="General description")
    alternate_names: List[str] = Field(default_factory=list)
    category: str = Field(
        description="armor, aircraft, naval, artillery, infantry_weapons, etc."
    )
    subcategory: Optional[str] = Field(
        default=None, description="e.g., medium_tank, fighter, destroyer"
    )
    country_of_origin: Optional[str] = Field(
        default=None,
        description=(
            "DESIGN/MANUFACTURE origin of this equipment TYPE — the country that "
            "built/designed it, NOT whoever is using it here (a Sherman is 'USA' "
            "even when used by the British). ISO 3166-1 alpha-3 (e.g., 'USA', "
            "'DEU', 'GBR', 'FRA', 'ITA', 'JPN', 'CAN')"
        ),
    )
    operating_country: Optional[str] = Field(
        default=None,
        description=(
            "Who was USING the equipment in THIS mention (may differ from origin: "
            "British using US Shermans -> 'GBR'; Germans using captured US gear -> "
            "'DEU'). ISO 3166-1 alpha-3. Per-mention, does NOT change the equipment "
            "type's identity."
        ),
    )
    captured: bool = Field(
        default=False,
        description=(
            "True if the equipment was CAPTURED and used against its origin (e.g. a "
            "German-operated captured US M10). Per-mention."
        ),
    )
    quantity: Optional[int] = Field(
        default=None,
        description=(
            "Exact number of THIS equipment stated for THIS mention (e.g. '10 M4 "
            "Shermans' -> 10). Null if not an exact number (use quantity_text for "
            "vague counts)."
        ),
    )
    quantity_text: Optional[str] = Field(
        default=None,
        description=(
            "Verbatim count phrase exactly as the source states it (e.g. 'several', "
            "'a handful', 'about a dozen', or '10'). Preserves vague counts without "
            "fabricating a number. Null if no count is stated."
        ),
    )
    place_name: Optional[str] = Field(
        default=None,
        description=(
            "Where this equipment was, per THIS mention (e.g. 'the crossroads in "
            "Cherbourg'). Strongly preferred but optional. Linked to a PlaceID when "
            "resolvable."
        ),
    )
    assertion_source: Optional[str] = Field(
        default=None,
        description=(
            "How the source ASSERTS this equipment was present: 'narrative' (stated "
            "in the text) or 'media_narration' (a video/audio narration explicitly "
            "says so). Only create a mention when the source ASSERTS presence; "
            "ambient/stock footage that does not assert it must NOT be extracted."
        ),
    )
    variants: List[Variant] = Field(default_factory=list)
    specifications: Optional[Dict[str, Any]] = Field(
        default=None, description="Technical specs"
    )
    using_unit_name: Optional[str] = Field(
        default=None, description="Unit using equipment"
    )
    using_person_name: Optional[str] = Field(
        default=None, description="Person using equipment"
    )
    performance_successes: List[str] = Field(default_factory=list)
    performance_failures: List[str] = Field(default_factory=list)
    field_modifications: List[str] = Field(default_factory=list)
    maintenance_issues: List[str] = Field(default_factory=list)
    variant_mentioned: Optional[str] = Field(
        default=None, description="Which variant in this event"
    )
    context: Optional[str] = Field(default=None, description="Brief situation summary")
    original_text: Optional[str] = Field(
        default=None, description="Text mentioning equipment"
    )
    paragraph_numbers: List[int] = Field(
        default_factory=list, description="Paragraph numbers where mentioned"
    )
    supporting_units: List["SupportingUnitInput"] = Field(
        default_factory=list,
        description=(
            "Supporting units in combined-arms ops (e.g. air support, artillery). Each "
            "has unit_name, support_type (the SUPPORTING unit's own arm — aircraft/"
            "artillery/armor — NOT this equipment's type), and the equipment it used."
        ),
    )
    related_equipment: List["RelatedEquipmentInput"] = Field(
        default_factory=list,
        description=(
            "Relationships the SOURCE draws between this equipment and another DISTINCT "
            "piece (predecessor/successor/variant). Only when the text asserts it. Do "
            "NOT list inline sub-designations here (M4A1, 'up-gunned M4' are variants)."
        ),
    )
    crew_accounts: List["CrewAccountInput"] = Field(
        default_factory=list,
        description=(
            "Firsthand crew accounts of operating this equipment that the SOURCE "
            "narrates. Each MUST carry original_text (verbatim) — source tracking is "
            "mandatory. Do not invent accounts."
        ),
    )
    environmental_performance: List["EnvironmentalPerformanceInput"] = Field(
        default_factory=list,
        description=(
            "How the equipment performed under weather/terrain CONDITIONS the SOURCE "
            "states (e.g. 'M4 performed badly in sub-zero temperatures'). Each MUST carry "
            "original_text. This is condition-linked PERFORMANCE — do NOT record the "
            "event's ambient weather here (that is the Weather entity's job)."
        ),
    )

    @field_validator("specifications", mode="before")
    @classmethod
    def validate_specifications(cls, v):  # pylint: disable=unused-argument
        """Convert string specifications to None."""
        _ = cls  # Used by decorator
        if isinstance(v, str):
            return None
        return v

    @field_validator("variants", mode="before")
    @classmethod
    def validate_variants(cls, v):  # pylint: disable=unused-argument
        """Convert string variants to empty list."""
        _ = cls  # Used by decorator
        if isinstance(v, str):
            return []
        if isinstance(v, list):
            # Filter out any string items
            return [item for item in v if isinstance(item, dict)]
        return v


def _load_json_files(
    directory: Path, skip_files: List[str]
) -> List[tuple[Path, Dict[str, Any]]]:
    """Load all JSON files from directory, skipping specified files."""
    results: List[tuple[Path, Dict[str, Any]]] = []
    if not directory.exists():
        return results

    for json_file in directory.glob("*.json"):
        if json_file.name in skip_files:
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                results.append((json_file, data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)
        except Exception as e:
            logger.debug("Skipping %s: %s", json_file.name, e)

    return results


def _build_people_index(output_root: Path) -> Dict[str, str]:
    """Build people name -> PersonID index."""
    people_dir = output_root / "people"
    skip_files = [
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        ".processed_events.json",
    ]

    index = {}
    for _, person_data in _load_json_files(people_dir, skip_files):
        if "PersonID" in person_data and "name" in person_data:
            index[person_data["name"]] = person_data["PersonID"]

    return index


def _build_groups_index(output_root: Path) -> Dict[str, str]:
    """Build group name -> GroupID index (includes aliases)."""
    groups_dir = output_root / "people_groups"
    skip_files = ["index.json", ".processed_events.json", "related_groups_report.json"]

    index = {}
    for _, group_data in _load_json_files(groups_dir, skip_files):
        group_id = group_data.get("GroupID") or group_data.get("PeopleGroupID")
        group_name = group_data.get("group_name") or group_data.get("name")

        if group_id and group_name:
            index[group_name] = group_id
            # Also index aliases
            for alias in group_data.get("aliases", []):
                index[alias] = group_id

    return index


def _build_dates_index(output_root: Path) -> Dict[str, Dict[str, str]]:
    """Build (EventID:Sub_eventID) -> DateID index."""
    dates_dir = output_root / "dates"
    skip_files = ["index.json"]

    index = {}
    for _, date_data in _load_json_files(dates_dir, skip_files):
        if "DateID" not in date_data:
            continue

        # Index by EventID + Sub_eventID for lookup
        for mention in date_data.get("event_mentions", []):
            if "EventID" in mention and "Sub_eventID" in mention:
                key = f"{mention['EventID']}:{mention['Sub_eventID']}"
                index[key] = {
                    "DateID": date_data["DateID"],
                    "DateMentionID": mention.get("DateMentionID")
                    or mention.get("MentionID"),
                }

    return index


def load_entity_indices(output_root: Path) -> tuple[dict, dict, dict]:
    """Load entity indices from output directory.

    Returns:
        Tuple of (people_index, people_groups_index, dates_index)

    Raises:
        FileNotFoundError: If output_root doesn't exist
    """
    if not output_root.exists():
        raise FileNotFoundError(f"Output root directory not found: {output_root}")

    people_index = _build_people_index(output_root)
    people_groups_index = _build_groups_index(output_root)
    dates_index = _build_dates_index(output_root)

    logger.info(
        "Loaded %s people, %s groups, %s date mentions",
        len(people_index),
        len(people_groups_index),
        len(dates_index),
    )
    return people_index, people_groups_index, dates_index


def load_equipment_index(equipment_dir: Path) -> Dict[str, Path]:
    """Load equipment index mapping name to file path.

    Returns:
        Dict mapping common_name to file path
    """
    index: Dict[str, Path] = {}
    if not equipment_dir.exists():
        return index

    for eq_file in equipment_dir.glob("*.json"):
        if eq_file.name in ("index.json", ".processed_events.json"):
            continue
        try:
            with open(eq_file, encoding="utf-8") as file_handle:
                eq_data = json.load(file_handle)
                if "common_name" in eq_data:
                    index[eq_data["common_name"]] = eq_file
        except Exception as e:
            logger.warning("Failed to load equipment file %s: %s", eq_file.name, e)

    return index


def _verify_media_with_vision(
    image_data: bytes,
    equipment_name: str,
    equipment_category: str,
    media_title: str,
    grok_client: GrokClient,
) -> tuple[bool, str]:
    """Verify media relevance using Grok vision API.

    Returns: (is_relevant, reason)
    """
    import base64
    from io import BytesIO

    from PIL import Image

    # Validate and convert image if needed
    try:
        img = Image.open(BytesIO(image_data))
        img.verify()
        img = Image.open(BytesIO(image_data))  # Reload after verify

        # Convert unsupported formats to PNG
        if img.format not in ["PNG", "JPEG", "JPG", "GIF"]:
            logger.debug("Converting %s to PNG", img.format)
            buffer = BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            image_data = buffer.getvalue()

        # Resize if too large
        size_mb = len(image_data) / (1024 * 1024)
        if size_mb > 5:
            logger.debug("Resizing image (%.1fMB → target <5MB)", size_mb)
            img = Image.open(BytesIO(image_data))

            scale = 0.7
            while size_mb > 5 and scale > 0.1:
                new_size = (int(img.width * scale), int(img.height * scale))
                resized = img.resize(new_size, Image.Resampling.LANCZOS)

                buffer = BytesIO()
                resized.save(buffer, format="PNG", optimize=True)
                image_data = buffer.getvalue()
                size_mb = len(image_data) / (1024 * 1024)
                scale -= 0.1

            if size_mb > 5:
                return False, f"Image too large ({size_mb:.1f}MB)"

    except Exception as e:
        return False, f"Invalid image: {e}"

    image_b64 = base64.b64encode(image_data).decode()

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "equipment_vision",
        equipment_name=equipment_name,
        equipment_category=equipment_category,
        image_title=media_title,
    )

    try:
        result = grok_client.extract_json_with_image_base64(
            prompt=prompt,
            image_base64=image_b64,
            cache_type="vision_verification",
            temperature=0.0,
        )

        if isinstance(result, dict):
            return result.get("is_relevant", False), result.get("reason", "Unknown")

    except Exception as e:
        logger.warning("Vision verification failed: %s", e)

    return False, "Verification failed"


def _extract_year_from_date(
    sub_event_id: Optional[str], dates_index: Optional[Dict[str, Dict[str, str]]]
) -> Optional[str]:
    """Extract year from date index."""
    if not sub_event_id or not dates_index or sub_event_id not in dates_index:
        return None

    date_info = dates_index[sub_event_id]
    date_str = date_info.get("date_start", "")

    if date_str and len(date_str) >= 4:
        return date_str[:4]  # Extract year (YYYY)

    return None


def _build_external_data(
    enriched: Dict[str, Any], equipment_data: Dict[str, Any]
) -> None:
    """Build external_data from enrichment URLs if not already present."""
    wiki_url = enriched.get("wikipedia_url")
    grok_url = enriched.get("grokipedia_url")
    if (wiki_url or grok_url) and "external_data" not in equipment_data:
        ext: Dict[str, Any] = {}
        if grok_url:
            ext["grokipedia_url"] = grok_url
        if wiki_url:
            ext["wikipedia_url"] = wiki_url
        equipment_data["external_data"] = ext


def _merge_enriched_data(
    equipment_data: Dict[str, Any], enriched: Dict[str, Any]
) -> None:
    """Merge enriched data into equipment data (don't overwrite existing)."""
    for key in ["description", "specifications", "alternate_names", "variants"]:
        if key in enriched and enriched[key]:
            if key not in equipment_data or not equipment_data[key]:
                equipment_data[key] = enriched[key]
                logger.debug("  Enriched %s: %s", key, type(enriched[key]).__name__)
    _build_external_data(enriched, equipment_data)
    _merge_source_tracked_reference(equipment_data, enriched)


def _merge_source_tracked_reference(
    equipment_data: Dict[str, Any], enriched: Dict[str, Any]
) -> None:
    """Populate enrichment-sourced reference facts (timeline / technical_evolution /
    logistics) and STAMP each with its source + source_url so even reference facts trace
    to where they came from. Gap-fill only (don't overwrite existing)."""
    source, source_url = _enrichment_source(enriched)
    # timeline + logistics are objects -> stamp source on the object
    for key in ("timeline", "logistics"):
        val = enriched.get(key)
        if isinstance(val, dict) and val and not equipment_data.get(key):
            val = {**val, "source": source, "source_url": source_url}
            equipment_data[key] = val
    # technical_evolution is a list of change records -> stamp source on each
    evo = enriched.get("technical_evolution")
    if isinstance(evo, list) and evo and not equipment_data.get("technical_evolution"):
        equipment_data["technical_evolution"] = [
            {**e, "source": source, "source_url": source_url}
            for e in evo
            if isinstance(e, dict)
        ]


def _enrichment_source(enriched: Dict[str, Any]) -> tuple:
    """Return (source_name, source_url) for provenance stamping based on which external
    URL the enrichment carried."""
    if enriched.get("grokipedia_url"):
        return "grokipedia", enriched["grokipedia_url"]
    if enriched.get("wikipedia_url"):
        return "wikipedia", enriched["wikipedia_url"]
    return "enrichment", None


def _add_downloaded_media(
    equipment_data: Dict[str, Any],
    media_list: list,
    common_name: str,
    grok_client: GrokClient,
    verify_media_with_vision: bool,
) -> None:
    """Download and add media to equipment data."""
    if not media_list:
        return

    media_dir = Path("filestore/equipment")
    downloaded_media = _download_and_store_media(
        media_list,
        common_name,
        equipment_data["category"],
        media_dir,
        grok_client,
        verify_with_vision=verify_media_with_vision,
    )

    if downloaded_media:
        equipment_data["media"] = downloaded_media
        # Also populate the structured images[] (first-class, schema-validated) so photos
        # are managed with provenance + trust markers, not just the legacy loose `media`.
        equipment_data["images"] = _to_structured_images(
            downloaded_media, verify_media_with_vision
        )
        logger.info("  Added %s verified media items", len(downloaded_media))


def _to_structured_images(
    media_items: list, vision_verified: bool
) -> List[Dict[str, Any]]:
    """Map downloaded media items to the structured images[] shape. image_scope defaults
    to 'representative' (a canonical TYPE reference image from Grokipedia/Wikipedia, not a
    documentary photo of a specific event); vision_verified reflects whether vision ran.
    """
    images = []
    for m in media_items:
        if m.get("media_type", "photo") != "photo":
            continue
        images.append(
            {
                "url": m.get("url"),
                "local_path": m.get("local_path"),
                "source": m.get("source"),
                "license": m.get("license"),
                "caption": m.get("title") or m.get("description"),
                "image_scope": "representative",
                "vision_verified": bool(vision_verified),
            }
        )
    return images


def _enrich_and_add_media(
    equipment_data: Dict[str, Any],
    common_name: str,
    grok_client: GrokClient,
    verify_media_with_vision: bool = True,
    sub_event_id: Optional[str] = None,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    """Enrich equipment data and add media files.

    Args:
        equipment_data: Equipment data dict (modified in place)
        common_name: Equipment common name
        grok_client: Grok API client
        verify_media_with_vision: Verify media relevance with Grok vision API
        sub_event_id: Sub-event ID to look up date
        dates_index: Index of dates by Sub-eventID
    """
    # Get year from date if available
    year = _extract_year_from_date(sub_event_id, dates_index)

    # Enrich equipment data
    logger.info("Enriching equipment data for: %s", common_name)
    enriched = _enrich_equipment_data(
        common_name,
        equipment_data.get("technical_identifier"),
        equipment_data["category"],
        grok_client,
    )
    _merge_enriched_data(equipment_data, enriched)

    # Extract and download media
    media_list = _extract_media(
        common_name,
        equipment_data.get("technical_identifier"),
        equipment_data["category"],
        grok_client,
        use_openserp=True,
        year=year,
    )
    _add_downloaded_media(
        equipment_data, media_list, common_name, grok_client, verify_media_with_vision
    )


def _download_and_store_media(
    media_list: List[Dict[str, Any]],
    equipment_name: str,
    equipment_category: str,
    media_dir: Path,
    grok_client: GrokClient,
    verify_with_vision: bool = True,
) -> List[Dict[str, Any]]:
    """Download media files and add local paths with deduplication.

    Args:
        media_list: List of media items with URLs
        equipment_name: Equipment name for logging
        equipment_category: Equipment category for verification
        media_dir: Base media directory
        grok_client: Grok API client for vision verification
        verify_with_vision: Whether to verify images with Grok vision API

    Returns:
        Media list with local_path added to verified/downloaded items (duplicates removed)
    """
    downloaded_media = []
    image_hashes: Dict[str, Tuple[str, str]] = {}  # hash -> (local_path, title)

    for media_item in media_list:
        local_path = _download_media_file(
            media_item,
            equipment_name,
            equipment_category,
            media_dir,
            grok_client,
            verify_with_vision,
        )
        if local_path:
            # Check for duplicate images using perceptual hash
            if media_item.get("media_type") == "photo":
                full_path = media_dir.parent / local_path
                img_hash = _compute_image_hash(full_path)

                if img_hash and img_hash in image_hashes:
                    # Duplicate found - remove the file
                    existing_path, existing_title = image_hashes[img_hash]
                    logger.info(
                        "  🗑️  Duplicate image removed: %s (same as %s)",
                        media_item.get("title", "Unknown"),
                        existing_title,
                    )
                    try:
                        full_path.unlink()
                        # Remove empty parent directory
                        if full_path.parent.exists() and not any(
                            full_path.parent.iterdir()
                        ):
                            full_path.parent.rmdir()
                    except Exception as e:
                        logger.debug("Failed to remove duplicate: %s", e)
                    continue
                elif img_hash:
                    # New unique image
                    image_hashes[img_hash] = (
                        local_path,
                        media_item.get("title", "Unknown"),
                    )

            media_item["local_path"] = local_path
            downloaded_media.append(media_item)
        else:
            logger.debug("Skipped media: %s", media_item.get("title", "Unknown"))

    return downloaded_media


def _compute_image_hash(image_path: Path) -> Optional[str]:
    """Compute perceptual hash for image deduplication.

    Args:
        image_path: Path to image file

    Returns:
        Hash string or None if failed
    """
    try:
        from PIL import Image
        import imagehash

        with Image.open(image_path) as img:
            # Use average hash (fast and effective for duplicates)
            return str(imagehash.average_hash(img))
    except Exception as e:
        logger.debug("Failed to compute hash for %s: %s", image_path.name, e)
        return None


def _determine_file_extension(response, url: str) -> str:
    """Determine file extension from content-type or URL."""
    content_type = response.headers.get("content-type", "")

    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    elif "png" in content_type:
        return ".png"
    elif "gif" in content_type:
        return ".gif"
    elif "webp" in content_type:
        return ".webp"
    elif "pdf" in content_type:
        return ".pdf"
    elif "mp4" in content_type or "video" in content_type:
        return ".mp4"
    else:
        # Fallback to URL extension
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return Path(parsed.path).suffix or ".jpg"


def _verify_and_save_media(
    response,
    filepath: Path,
    equipment_dir: Path,
    media_item: Dict[str, Any],
    equipment_name: str,
    equipment_category: str,
    grok_client: GrokClient,
    verify_with_vision: bool,
) -> bool:
    """Verify media with vision API and save if relevant."""
    # Verify with vision API if enabled
    if verify_with_vision and media_item.get("media_type") == "photo":
        is_relevant, reason = _verify_media_with_vision(
            response.content,
            equipment_name,
            equipment_category,
            media_item.get("title", ""),
            grok_client,
        )
        if not is_relevant:
            logger.info("  ⚠️  Rejected: %s", reason)
            return False
        logger.info("  ✅ Verified: %s", reason)

    # Create directory and save file
    equipment_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(response.content)

    logger.debug("Downloaded media: %s", filepath.name)
    return True


def _cleanup_empty_directory(equipment_dir: Path) -> None:
    """Clean up empty directory if download failed."""
    if equipment_dir.exists() and not any(equipment_dir.iterdir()):
        try:
            equipment_dir.rmdir()
            logger.debug("Cleaned up empty directory: %s", equipment_dir.name)
        except Exception:
            logger.debug("Could not remove empty directory: %s", equipment_dir.name)


def _download_media_file(
    media_item: Dict[str, Any],
    equipment_name: str,
    equipment_category: str,
    media_dir: Path,
    grok_client: GrokClient,
    verify_with_vision: bool = True,
) -> Optional[str]:
    """Download media file to local storage with vision verification.

    Args:
        media_item: Media item with URL
        equipment_name: Equipment name for subdirectory
        equipment_category: Equipment category for verification
        media_dir: Base media directory (/filestore)
        grok_client: Grok API client for vision verification
        verify_with_vision: Whether to verify images with Grok vision API

    Returns:
        Relative path to downloaded file or None
    """
    import requests

    url = media_item.get("url")
    if not url:
        return None

    # Create equipment subdirectory
    media_id = str(ulid.new())
    equipment_dir = media_dir / media_id

    try:
        # Download file with User-Agent header
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        session = get_session()
        response = session.get(url, timeout=30, headers=headers, allow_redirects=True)
        response.raise_for_status()

        # Determine file extension and generate filename
        ext = _determine_file_extension(response, url)
        filename = f"{media_id}{ext}"
        filepath = equipment_dir / filename

        # Check if already downloaded
        if filepath.exists():
            logger.debug("Media already downloaded: %s", filename)
            return str(filepath.relative_to(media_dir.parent))

        # Verify and save
        if not _verify_and_save_media(
            response,
            filepath,
            equipment_dir,
            media_item,
            equipment_name,
            equipment_category,
            grok_client,
            verify_with_vision,
        ):
            return None

        return str(filepath.relative_to(media_dir.parent))

    except requests.RequestException as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None
    except Exception as e:
        logger.debug("Media download error: %s", e)
        return None
    finally:
        _cleanup_empty_directory(equipment_dir)


def _extract_media(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
    use_openserp: bool = True,
    year: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract media using OpenSERP (preferred) or Wikipedia fallback.

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client
        use_openserp: Try OpenSERP first
        year: Year for temporal filtering (e.g., "1944")

    Returns:
        List of media items with URLs
    """
    media_list = []

    # Try OpenSERP first (real search engines, no hallucinations)
    if use_openserp:
        media_list = _extract_media_with_openserp(
            common_name, technical_identifier, category, grok_client, year
        )
        if media_list:
            logger.debug("Using OpenSERP media for %s", common_name)
            return media_list

    # Fallback to Wikipedia/Grokipedia
    media_list = _extract_media_from_wikipedia(
        common_name, technical_identifier, category, grok_client
    )
    if media_list:
        logger.debug("Using Wikipedia media for %s", common_name)

    return media_list


def _extract_image_urls_from_page(
    page_url: str, equipment_name: str, grok_client: GrokClient
) -> List[str]:
    """Extract actual image URLs from a wiki page using Grok.

    Args:
        page_url: URL of the wiki page
        equipment_name: Equipment name for context
        grok_client: Grok API client

    Returns:
        List of direct image URLs
    """
    try:
        # Fetch page content with standard browser headers
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        session = get_session()
        response = session.get(
            page_url, timeout=30, headers=headers, allow_redirects=True
        )
        response.raise_for_status()
        page_content = response.text

        # Ask Grok to extract image URLs
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "equipment_urls", equipment_name=equipment_name, page_content=page_content
        )

        result = grok_client.extract_json(
            prompt=prompt, cache_type="equipment_image_extraction", temperature=0.0
        )

        if isinstance(result, list):
            # Filter for valid image URLs
            image_urls = []
            for url in result:
                if isinstance(url, str) and url.startswith("http"):
                    # Ensure it's a direct image URL
                    if any(
                        ext in url.lower()
                        for ext in [".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp"]
                    ):
                        image_urls.append(url)
            return image_urls

    except Exception as e:
        logger.debug("Failed to extract images from %s: %s", page_url, e)

    return []


def _build_search_query(
    common_name: str, technical_identifier: Optional[str], year: Optional[str]
) -> str:
    """Build search query for OpenSERP."""
    identifier = technical_identifier or common_name
    year_str = year if year else "1939-1945"
    return f"{identifier} {common_name} WWII {year_str} photo wikipedia commons"


def _run_openserp_search(search_query: str) -> list:
    """Run OpenSERP search and return results."""
    import subprocess  # nosec B404

    try:
        result = subprocess.run(  # nosec B603 B404
            ["./tools/search_media", search_query],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            logger.debug("OpenSERP search failed: %s", result.stderr)
            return []

        return json.loads(result.stdout)

    except FileNotFoundError:
        logger.debug("OpenSERP tool not found, skipping")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("OpenSERP search timed out")
        return []
    except json.JSONDecodeError as e:
        logger.debug("Failed to parse OpenSERP response: %s", e)
        return []


def _extract_images_from_pages(
    page_results: list, common_name: str, grok_client: GrokClient
) -> list:
    """Extract image URLs from wiki pages."""
    media_list = []

    for page in page_results[:3]:  # Limit to first 3 pages
        if not isinstance(page, dict) or "url" not in page:
            continue

        page_url = page["url"]
        image_urls = _extract_image_urls_from_page(page_url, common_name, grok_client)

        for img_url in image_urls[:2]:  # Max 2 images per page
            media_list.append(
                {
                    "media_type": "photo",
                    "url": img_url,
                    "title": page.get("title", ""),
                    "source": page.get("source", "unknown"),
                    "license": "See source",
                    "description": f"From {page_url}",
                }
            )

    return media_list


def _extract_media_with_openserp(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
    year: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract media URLs using OpenSERP (real search engines).

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client for extracting images from pages
        year: Year for temporal filtering (e.g., "1944")

    Returns:
        List of media items with direct image URLs
    """
    search_query = _build_search_query(common_name, technical_identifier, year)

    try:
        page_results = _run_openserp_search(search_query)
        if not page_results:
            return []

        logger.debug(
            "Found %s wiki pages via OpenSERP for %s", len(page_results), common_name
        )

        media_list = _extract_images_from_pages(page_results, common_name, grok_client)
        logger.debug("Extracted %s image URLs from wiki pages", len(media_list))

        return media_list

    except Exception as e:
        logger.debug("OpenSERP search error: %s", e)
        return []


def _extract_media_from_wikipedia(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> List[Dict[str, Any]]:
    """Extract images from the actual Wikipedia article for this equipment.

    Finds the Wikipedia page, gets its images via the API, then returns
    real download URLs. Grok vision validates relevance downstream.
    """
    identifier = technical_identifier or common_name
    session = get_session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    try:
        # Step 1: Find the Wikipedia article
        search_resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": identifier,
                "srlimit": "1",
            },
            timeout=15,
            headers=headers,
        )
        search_resp.raise_for_status()
        results = search_resp.json().get("query", {}).get("search", [])
        if not results:
            logger.debug("No Wikipedia article for %s", identifier)
            return []

        page_title = results[0]["title"]

        # Step 2: Get images from that article
        img_resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": page_title,
                "prop": "images",
                "imlimit": "20",
            },
            timeout=15,
            headers=headers,
        )
        img_resp.raise_for_status()
        pages = img_resp.json().get("query", {}).get("pages", {})
        image_titles = []
        for page in pages.values():
            for img in page.get("images", []):
                title = img.get("title", "")
                # Skip icons/logos/commons junk
                if any(
                    skip in title.lower()
                    for skip in [
                        "icon",
                        "logo",
                        "flag",
                        "symbol",
                        "commons-",
                        "edit-",
                        "question_book",
                        "wikiproject",
                        "padlock",
                        "ambox",
                    ]
                ):
                    continue
                if title.lower().endswith((".jpg", ".jpeg", ".png", ".svg")):
                    image_titles.append(title)

        if not image_titles:
            logger.debug("No images on Wikipedia page for %s", identifier)
            return []

        # Step 3: Get actual file URLs from Commons (batch up to 5)
        file_resp = session.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": "|".join(image_titles[:5]),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
                "iiurlwidth": "1280",
            },
            timeout=15,
            headers=headers,
        )
        file_resp.raise_for_status()
        file_pages = file_resp.json().get("query", {}).get("pages", {})

        media_list = []
        for fp in file_pages.values():
            info = (fp.get("imageinfo") or [{}])[0]
            mime = info.get("mime", "")
            if not mime.startswith("image/"):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            meta = info.get("extmetadata", {})
            media_list.append(
                {
                    "media_type": "photo",
                    "url": url,
                    "title": fp.get("title", "").replace("File:", ""),
                    "source": "commons",
                    "license": meta.get("LicenseShortName", {}).get("value", "Unknown"),
                    "description": (
                        meta.get("ImageDescription", {}).get("value", "") or ""
                    )[:200],
                }
            )

        logger.debug(
            "Found %s images from Wikipedia article '%s'", len(media_list), page_title
        )
        return media_list
    except Exception as e:
        logger.warning("Wikipedia media lookup failed for %s: %s", common_name, e)
        return []


def _enrich_equipment_data(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> Dict[str, Any]:
    """Enrich equipment data with external sources (Wikipedia/Grokipedia).

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client

    Returns:
        Dict with enriched data (description, specifications, etc.)
    """
    identifier = technical_identifier or common_name

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "equipment_enrichment",
        identifier=identifier,
        common_name=common_name,
        category=category,
        country="",
    )

    try:
        # Use extract_json: the model wraps JSON in a ```json fence, which a bare
        # json.loads(chat_completion(...)) cannot parse (it silently returned {} for
        # every record — caught by a live run). extract_json strips the fence.
        enriched = grok_client.extract_json(
            prompt,
            temperature=0.1,
            use_cache=True,
            cache_type="equipment_enrichment",
        )
        logger.debug("Enriched data for %s", common_name)
        return enriched if isinstance(enriched, dict) else {}
    except Exception as e:
        logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
        return {}


def _fuzzy_match_equipment(
    name: str, equipment_index: Dict[str, Path], threshold: float = 0.80
) -> Optional[str]:
    """Find best fuzzy match for equipment name.

    Checks both common_name and alternate_names from equipment files.

    Args:
        name: Equipment name to match
        equipment_index: Index of existing equipment
        threshold: Minimum similarity ratio (0.0-1.0), default 0.80

    Returns:
        Matched equipment name or None
    """
    if not equipment_index:
        return None

    best_match = None
    best_ratio = 0.0

    name_lower = name.lower()

    # Check common names
    for existing_name in equipment_index.keys():
        ratio = SequenceMatcher(None, name_lower, existing_name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing_name

    # Also check alternate names in files
    for existing_name, eq_file in equipment_index.items():
        try:
            with open(eq_file, encoding="utf-8") as f:
                eq_data = json.load(f)
                for alt_name in eq_data.get("alternate_names", []):
                    ratio = SequenceMatcher(None, name_lower, alt_name.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = existing_name
        except Exception:  # nosec B112
            continue  # Skip invalid entries

    if best_ratio >= threshold:
        logger.debug("Fuzzy matched '%s' to '%s' (%.2f)", name, best_match, best_ratio)
        return best_match

    return None


def _find_matching_equipment(
    common_name: str,
    equipment_index: Dict[str, Path],
    technical_id: str = "",
    canonical_name: str = "",
) -> Optional[str]:
    """Find matching equipment. Canonical identity (resolved across US/German/British
    naming systems) is the MOST stable key, so check it first — this is what lets the
    disambiguator actually prevent cross-naming splits (M4 vs Sherman V -> one record).
    """
    if canonical_name and canonical_name in equipment_index:
        return canonical_name
    # Check technical_identifier next (stable)
    if technical_id and technical_id in equipment_index:
        return technical_id
    if common_name in equipment_index:
        return common_name
    return _fuzzy_match_equipment(common_name, equipment_index)


def _merge_equipment_fields(existing: dict, equipment_data: dict) -> None:
    """Merge equipment fields from new data into existing."""
    for key in [
        "description",
        "alternate_names",
        "subcategory",
        "variants",
        "specifications",
    ]:
        if key not in equipment_data or not equipment_data[key]:
            continue

        if key == "alternate_names" and key in existing:
            # Merge alternate names
            existing[key] = list(set(existing[key] + equipment_data[key]))
        elif key == "variants" and key in existing:
            # Merge variants by variant_name
            existing_variants = {
                v["variant_name"]: v for v in existing.get("variants", [])
            }
            for new_variant in equipment_data.get("variants", []):
                existing_variants[new_variant["variant_name"]] = new_variant
            existing["variants"] = list(existing_variants.values())
        else:
            existing[key] = equipment_data[key]

    _merge_related_equipment(existing, equipment_data)
    _merge_crew_accounts(existing, equipment_data)
    _merge_environmental_performance(existing, equipment_data)


def _merge_environmental_performance(existing: dict, equipment_data: dict) -> None:
    """Accumulate condition-linked environmental_performance across mentions, deduped by
    (condition, original_text). Conflicts/different conditions are all kept."""
    incoming = equipment_data.get("environmental_performance") or []
    if not incoming:
        return
    merged = list(existing.get("environmental_performance") or [])
    seen = {
        ((e.get("condition") or "").lower(), e.get("original_text") or "")
        for e in merged
    }
    for ep in incoming:
        k = ((ep.get("condition") or "").lower(), ep.get("original_text") or "")
        if k not in seen:
            merged.append(ep)
            seen.add(k)
    existing["environmental_performance"] = merged


def _merge_crew_accounts(existing: dict, equipment_data: dict) -> None:
    """Accumulate narrative-sourced crew_accounts across mentions, deduped by
    (person_name, original_text). Each account keeps its own source (original_text+book).
    """
    incoming = equipment_data.get("crew_accounts") or []
    if not incoming:
        return
    merged = list(existing.get("crew_accounts") or [])
    seen = {
        ((a.get("person_name") or "").lower(), a.get("original_text") or "")
        for a in merged
    }
    for acc in incoming:
        k = ((acc.get("person_name") or "").lower(), acc.get("original_text") or "")
        if k not in seen:
            merged.append(acc)
            seen.add(k)
    existing["crew_accounts"] = merged


def _merge_related_equipment(existing: dict, equipment_data: dict) -> None:
    """Accumulate narrative-sourced related_equipment across mentions, deduped by
    (relationship, lowercased name). New relationships are added; exact duplicates
    collapse (conflicting/different relationships are all kept — each is traceable)."""
    incoming = equipment_data.get("related_equipment") or []
    if not incoming:
        return
    merged = list(existing.get("related_equipment") or [])
    seen = {(r.get("relationship"), (r.get("name") or "").lower()) for r in merged}
    for rel in incoming:
        key = (rel.get("relationship"), (rel.get("name") or "").lower())
        if key not in seen:
            merged.append(rel)
            seen.add(key)
    existing["related_equipment"] = merged


def _merge_into_existing(
    eq_file: Path,
    new_mention: dict,
    equipment_data: dict,
    matched_name: str,
    grok_client: Optional[GrokClient] = None,
    verify_media_with_vision: bool = True,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Merge mention into existing equipment file."""
    logger.debug("Merging mention into existing equipment: %s", matched_name)

    # Load existing
    from src.utils.file_lock import locked_json
    from src.schemas.schema_contract import read_for_update

    with locked_json(eq_file) as (existing, save):
        # Read the historical record under this module's schema contract: a FUTURE-schema
        # record is skipped gracefully (code not built for it); older records merge
        # best-effort (upgrade case-by-case if registered).
        existing, skip = read_for_update(existing, SCHEMA_TARGET, logger, matched_name)
        if not skip:
            # Check if mention already exists (semantic dedup by event+sub-event)
            existing_keys = {
                (m.get("EventID"), m.get("Sub_eventID"))
                for m in existing.get("event_mentions", [])
            }
            new_key = (new_mention.get("EventID"), new_mention.get("Sub_eventID"))
            if new_key in existing_keys:
                logger.debug("Mention for %s already exists, skipping", new_key)
                return eq_file

            # Append mention
            existing["event_mentions"].append(new_mention)

            # Update optional fields
            _merge_equipment_fields(existing, equipment_data)

            # Enrichment RETRY: a record whose first enrichment FAILED was saved without an
            # enrichment_status stamp. A later mention is our chance to retry (idempotent —
            # _enrich_on_identity no-ops once stamped). Already-enriched records are untouched.
            if grok_client and not existing.get("enrichment_status"):
                _enrich_on_identity(
                    existing,
                    grok_client,
                    verify_media_with_vision,
                    new_mention.get("Sub_eventID"),
                    dates_index,
                )

            # Stamp last-modified (equipment previously only set extracted_date on create).
            existing["_last_updated"] = datetime.now(timezone.utc).date().isoformat()

            # Save
            save(existing)

    return eq_file


# Generic/category words + classification phrases that, alone, do NOT constitute a
# specific identity. Includes subcategory classes ("medium tank" vs "M4" — a common
# disambiguation: the category is NOT the specific vehicle).
_GENERIC_EQUIPMENT_WORDS = {
    "tank",
    "tanks",
    "gun",
    "guns",
    "machine gun",
    "machine guns",
    "artillery",
    "aircraft",
    "plane",
    "planes",
    "vehicle",
    "vehicles",
    "truck",
    "trucks",
    "weapon",
    "weapons",
    "rifle",
    "rifles",
    "equipment",
    "armor",
    "cannon",
    "howitzer",
    "mortar",
    # category / subcategory CLASSIFICATION phrases (not a specific designation)
    "medium tank",
    "heavy tank",
    "light tank",
    "tank destroyer",
    "armored car",
    "armoured car",
    "self-propelled gun",
    "assault gun",
    "field gun",
    "field howitzer",
    "anti-tank gun",
    "anti-aircraft gun",
    "anti-tank",
    "anti-aircraft",
    "fighter",
    "bomber",
    "fighter-bomber",
    "fighter bomber",
    "automatic rifle",
    "submachine gun",
    "half-track",
    "half track",
    "utility vehicle",
    "landing craft",
    "artillery piece",
    "field piece",
}


def _normalize_generic(name: str) -> str:
    """Normalize a name for generic-phrase comparison: underscores->spaces, collapse ws."""
    import re as _re

    return _re.sub(r"\s+", " ", name.replace("_", " ")).strip()


def _is_specific_identity(equipment_data: dict) -> bool:
    """True if the record has resolved to a SPECIFIC designation worth enriching
    (has a technical_identifier, a nickname that resolves to a canonical technical name
    via the alias table, or a common_name that is not a bare generic word)."""
    if equipment_data.get("technical_identifier"):
        return True
    name = (equipment_data.get("common_name") or "").strip().lower()
    if not name:
        return False
    # A nickname that resolves via the alias table (sherman -> m4 sherman, 88 -> 88mm
    # flak 36) is a specific identity expressed informally.
    if _normalize_designation(name) in _equipment_aliases():
        return True
    # A bare category/subcategory classification ("medium tank", "medium_tank",
    # "field gun") is NOT a specific identity — it's a class, not a designation.
    return _normalize_generic(name) not in _GENERIC_EQUIPMENT_WORDS


def _normalize_designation(name: str) -> str:
    """Normalize a designation so one curated alias matches surface variants:
    lowercase; unify Pz.Kpfw./PzKpfw/Panzerkampfwagen -> panzer; strip Sd.Kfz. punctuation;
    collapse punctuation/spacing. (roman<->arabic is handled separately where needed.)
    """
    import re as _re

    s = name.lower().strip()
    s = _re.sub(r"\bpanzerkampfwagen\b", "panzer", s)
    s = _re.sub(r"\bpz\.?\s*kpfw\.?\b", "panzer", s)
    s = _re.sub(r"\bpzkpfw\b", "panzer", s)
    s = _re.sub(r"\bsd\.?\s*kfz\.?\b", "sdkfz", s)
    s = s.replace(".", " ")
    s = _re.sub(r"[\s_]+", " ", s).strip()
    return s


def _equipment_aliases() -> Dict[str, str]:
    """Cached nickname/abbreviation -> canonical technical name map (config-driven).
    Keys are NORMALIZED (_normalize_designation) so one entry covers surface variants.
    """
    global _EQUIPMENT_ALIAS_CACHE
    if _EQUIPMENT_ALIAS_CACHE is None:
        import yaml

        alias_file = (
            Path(__file__).parent.parent.parent / "config" / "equipment_aliases.yaml"
        )
        try:
            data = yaml.safe_load(alias_file.read_text(encoding="utf-8"))
            _EQUIPMENT_ALIAS_CACHE = {
                _normalize_designation(k): v.lower()
                for k, v in (data.get("aliases") or {}).items()
            }
        except Exception:  # noqa: BLE001 - absent/malformed table -> no aliases
            _EQUIPMENT_ALIAS_CACHE = {}
    return _EQUIPMENT_ALIAS_CACHE


def _canonical_equipment_name(name: str) -> str:
    """Resolve a nickname to its canonical technical name for enrichment lookups
    (sherman -> m4 sherman); unchanged if not an alias."""
    if not name:
        return name
    return _equipment_aliases().get(_normalize_designation(name), name)


_EQUIPMENT_ALIAS_CACHE: Optional[Dict[str, str]] = None


def _resolve_canonical_identity(
    equipment_data: dict, grok_client: Optional[GrokClient]
) -> str:
    """Resolve the record's designation to a canonical identity
    (exact→alias→learned→fuzzy→Grok) and stamp canonical_name/identity_source/
    country_of_origin. Returns the name to use for enrichment. Idempotent: if already
    resolved, returns the stamped canonical_name without re-resolving."""
    if equipment_data.get("canonical_name"):
        return equipment_data["canonical_name"]
    from src.extraction.equipment_disambiguation import resolve_designation

    canonical_name = _canonical_equipment_name(equipment_data["common_name"])
    ident = resolve_designation(equipment_data["common_name"], grok_client)
    if ident and ident.get("identity_source") not in (None, "raw"):
        canonical_name = ident["canonical_name"]
        equipment_data["canonical_name"] = canonical_name
        equipment_data["identity_source"] = ident["identity_source"]
        if ident.get("nationality_of_origin") and not equipment_data.get(
            "country_of_origin"
        ):
            equipment_data["country_of_origin"] = _normalize_origin(
                ident["nationality_of_origin"]
            )
    return canonical_name


def _normalize_origin(nationality: Optional[str]) -> Optional[str]:
    """Normalize a free-text origin to a canonical ISO alpha-3 (USSR/Soviet/Russia -> SUN,
    etc.) via the shared award-registry mapper, so dedup's origin veto compares consistent
    codes. Falls back to the uppercased input when unmapped (never drops a stated value).
    """
    if not nationality:
        return nationality
    try:
        from src.enrichment.award_sources import canonical_nationality

        return canonical_nationality(nationality) or nationality.strip().upper()
    except Exception:  # noqa: BLE001 - normalization is best-effort
        return nationality.strip().upper()


def _enrich_on_identity(
    equipment_data: dict,
    grok_client: Optional[GrokClient],
    verify_media_with_vision: bool,
    sub_event_id: Optional[str],
    dates_index: Optional[Dict[str, Dict[str, str]]],
) -> None:
    """Grokipedia/Wikipedia enrichment + canonical reference image, triggered by identity
    resolution. Fires once per SPECIFIC record (never re-enriches: enrichment_status
    stamp). Vision verifies the equipment TYPE for the reference image, never an event.
    """
    if not grok_client:
        return
    if equipment_data.get("enrichment_status") == "enriched":
        return  # already successfully enriched — never re-enrich
    if not _is_specific_identity(equipment_data):
        logger.debug(
            "Skipping enrichment for non-specific equipment: %s",
            equipment_data.get("common_name"),
        )
        return
    # CANONICAL DISAMBIGUATION (exact→alias→fuzzy→Grok, cached): resolve to a canonical
    # identity so enrichment/dedup use one name across US/German/British naming systems.
    canonical_name = _resolve_canonical_identity(equipment_data, grok_client)
    # LIMIT UPDATES: skip if we checked Grokipedia/Wikipedia within the staleness window.
    from src.enrichment.enrichment_gate import (
        diff_enrichment,
        should_check_enrichment,
        stamp_checked,
    )

    if not should_check_enrichment(equipment_data):
        logger.debug(
            "Skipping enrichment (checked recently): %s",
            equipment_data.get("common_name"),
        )
        return
    try:
        before = {k: v for k, v in equipment_data.items()}
        _enrich_and_add_media(
            equipment_data,
            canonical_name,
            grok_client,
            verify_media_with_vision,
            sub_event_id,
            dates_index,
        )
        # DIFF the revised entry: which keys did enrichment actually change/add?
        changed = diff_enrichment(before, equipment_data)
        equipment_data["enrichment_status"] = "enriched"
        stamp_checked(equipment_data)  # stamp WHEN we checked (all grok/wiki checks)
        if changed:
            logger.info(
                "Enrichment updated %s: %s", equipment_data["common_name"], changed
            )
        else:
            logger.debug(
                "Enrichment no-op for %s (no new data)", equipment_data["common_name"]
            )
    except Exception as e:  # noqa: BLE001 - enrichment is best-effort, never block
        # Stamp the CHECK even on failure so the staleness gate still advances (limit
        # updates); leave enrichment_status unset so a later run can still retry.
        stamp_checked(equipment_data)
        logger.warning(
            "Identity enrichment failed for %s: %s",
            equipment_data.get("common_name"),
            e,
        )


def _create_new_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient],
    enable_enrichment: bool,
    verify_media_with_vision: bool,
    dates_index: Optional[Dict[str, Dict[str, str]]],
) -> Path:
    """Create new equipment file."""
    common_name = equipment_data["common_name"]
    logger.debug("Creating new equipment file: %s", common_name)

    # Enrichment follows IDENTITY RESOLUTION: once a record has a specific designation
    # (M4 Sherman, M2 .50 cal), Grokipedia/Wikipedia enrichment + a canonical reference
    # image are the natural next step — not an opt-in flag. Generic records (bare "tank")
    # are skipped. Enriched once, stamped (never re-enriched).
    _enrich_on_identity(
        equipment_data,
        grok_client,
        verify_media_with_vision,
        new_mention.get("Sub_eventID"),
        dates_index,
    )

    equipment_id = str(ulid.new())
    equipment_data["EquipmentID"] = equipment_id
    equipment_data["event_mentions"] = [new_mention]
    equipment_data["extracted_date"] = datetime.now(timezone.utc).isoformat()
    equipment_data["_last_updated"] = datetime.now(timezone.utc).date().isoformat()

    safe_name = common_name.replace(" ", "_").replace("/", "_")
    eq_file = equipment_dir / f"{safe_name}_{equipment_id[:8]}.json"

    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(eq_file, equipment_data)

    # Update index (prefer technical_identifier for stability)
    index_key = equipment_data.get("technical_identifier") or common_name
    equipment_index[index_key] = eq_file
    # Also index by common_name for lookup compatibility
    if index_key != common_name:
        equipment_index[common_name] = eq_file
    # And by canonical identity, so a later mention under a different national designation
    # (M4 vs Sherman V) resolves to THIS record.
    canonical = equipment_data.get("canonical_name")
    if canonical and canonical not in equipment_index:
        equipment_index[canonical] = eq_file

    return eq_file


def merge_or_create_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient] = None,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Merge mention into existing equipment or create new file.

    Args:
        equipment_data: Equipment data (common_name, category, etc.)
        new_mention: New mention to add
        equipment_dir: Output directory
        equipment_index: Index of existing equipment
        grok_client: Grok API client for enrichment
        enable_enrichment: Whether to enrich new equipment with external data
        verify_media_with_vision: Verify media relevance with Grok vision API
        dates_index: Index of dates by Sub-eventID for temporal filtering

    Returns:
        Path to equipment file
    """
    common_name = equipment_data["common_name"]

    # Find matching equipment (canonical identity is the most stable key)
    technical_id = equipment_data.get("technical_identifier", "")
    canonical = equipment_data.get("canonical_name", "")
    matched_name = _find_matching_equipment(
        common_name, equipment_index, technical_id, canonical
    )

    if matched_name:
        eq_file = equipment_index[matched_name]
        return _merge_into_existing(
            eq_file,
            new_mention,
            equipment_data,
            matched_name,
            grok_client,
            verify_media_with_vision,
            dates_index,
        )
    else:
        return _create_new_equipment(
            equipment_data,
            new_mention,
            equipment_dir,
            equipment_index,
            grok_client,
            enable_enrichment,
            verify_media_with_vision,
            dates_index,
        )


def _validate_event_data(event_data: Dict[str, Any], event_file: Path) -> bool:
    """Validate event data structure. Returns True if valid."""
    if "Event" not in event_data:
        logger.error("Missing 'Event' key in %s", event_file)
        return False
    if "EventID" not in event_data["Event"]:
        logger.error("Missing 'EventID' in %s", event_file)
        return False
    return True


def _extract_equipment_with_llm(
    event_data: Dict[str, Any], grok_client: GrokClient, max_retries: int
) -> Optional[List[Dict[str, Any]]]:
    """Extract equipment using LLM with retry logic."""
    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt("equipment", text=json.dumps(event_data, indent=2))

    for attempt in range(max_retries):
        try:
            equipment_list = grok_client.extract_json(
                prompt,
                temperature=0.1,
                use_cache=(attempt == 0),
                cache_type="equipment",
            )

            if isinstance(equipment_list, dict) and "equipment" in equipment_list:
                equipment_list = equipment_list["equipment"]

            if isinstance(equipment_list, list):
                return _fix_invalid_ulids(equipment_list)

            return None

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("  ⚠ Attempt %s failed: %s", attempt + 1, e)
                logger.info("  Retrying (%s/%s)...", attempt + 2, max_retries)
            else:
                import os

                if os.environ.get("PIPELINE_PHASE"):
                    logger.info(
                        "  ⊘ Sync equipment fallback skipped (batch mode): %s", e
                    )
                else:
                    logger.error("  ✗ All %s attempts failed: %s", max_retries, e)

    return None


def _find_sub_event(
    event_data: Dict[str, Any], paragraph_numbers: List[int]
) -> Dict[str, Any]:
    """Find the sub-event matching the given paragraph numbers."""
    sub_events = event_data["Event"].get("Sub-events", [])
    if not sub_events:
        return {}
    if not paragraph_numbers:
        return sub_events[0]

    # Match by paragraph number in fulltext keys
    for se in sub_events:
        fulltext = se.get("Sub-event_fulltext", {})
        if isinstance(fulltext, dict):
            for key in fulltext:
                try:
                    para_num = int(key.split("_")[-1]) if "_" in key else int(key)
                    if para_num in paragraph_numbers:
                        return se
                except (ValueError, IndexError):
                    continue
    return sub_events[0]


def _build_mention(
    eq: EquipmentExtraction,
    event_data: Dict[str, Any],
    using_unit: Optional[Dict[str, str]],
    using_person: Optional[Dict[str, str]],
    performance_notes: Optional[Dict[str, List[str]]],
    supporting_units: List[Dict[str, Any]],
    dates_index: Dict[str, Dict[str, str]],
    output_root: Path,
    places_index: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build equipment mention with all metadata."""
    sub_event = _find_sub_event(event_data, eq.paragraph_numbers)
    mention_id = str(ulid.new())
    mention = {
        "MentionID": mention_id,
        "EventID": event_data["Event"]["EventID"],
        "Sub_eventID": sub_event.get("Sub-eventID", str(ulid.new())),
    }

    # Add metadata and event names
    _add_metadata_to_mention(mention, event_data)
    _add_event_names_to_mention(mention, event_data)

    _populate_mention_fields(mention, eq, sub_event, places_index)

    # Link to date
    _link_date_to_mention(mention, dates_index, output_root)

    # Add linked entities
    if using_unit:
        mention["using_unit"] = using_unit
    if using_person:
        mention["using_person"] = using_person
    if supporting_units:
        mention["supporting_units"] = supporting_units
    if performance_notes:
        mention["performance_notes"] = performance_notes

    return mention


def _populate_mention_fields(
    mention: Dict[str, Any],
    eq: EquipmentExtraction,
    sub_event: Dict[str, Any],
    places_index: Optional[Dict[str, str]],
) -> None:
    """Populate the equipment-specific per-mention fields (quantity/operator/place/…)."""
    if eq.paragraph_numbers:
        mention["paragraph_numbers"] = eq.paragraph_numbers
    if eq.variant_mentioned:
        mention["variant_mentioned"] = eq.variant_mentioned
    if eq.context:
        mention["context"] = eq.context
    if eq.original_text:
        mention["original_text"] = eq.original_text
    # Per-mention operator (distinct from the equipment type's country_of_origin)
    if eq.operating_country:
        mention["operating_country"] = eq.operating_country
    if eq.captured:
        mention["captured"] = True
    # Per-mention quantity: exact number AND/OR the verbatim count phrase.
    if eq.quantity is not None:
        mention["quantity"] = eq.quantity
    if eq.quantity_text:
        mention["quantity_text"] = eq.quantity_text
    _resolve_mention_place(mention, eq, sub_event, places_index)
    if eq.assertion_source:
        mention["assertion_source"] = eq.assertion_source


def _resolve_place_id(
    place_name: str, places_index: Optional[Dict[str, str]]
) -> Optional[str]:
    """Resolve a stated place_name to a single PlaceID, fuzzily.

    The index is alias-aware (keyed on current_name + aliases, lowercased). Matching, in
    order of confidence: exact -> whole-word containment (longest key) -> SequenceMatcher
    ratio >= 0.88 (conservative — guards against e.g. Carentan vs Cherbourg). Below
    threshold -> no match (leave PlaceID null; never guess).
    """
    if not place_name or not places_index:
        return None
    name = place_name.lower().strip()
    if name in places_index:
        return places_index[name]
    return _place_contains_match(name, places_index) or _place_fuzzy_match(
        name, places_index
    )


def _place_contains_match(name: str, places_index: Dict[str, str]) -> Optional[str]:
    """Longest index key that appears as a whole-word phrase in the stated name."""
    import re as _re

    best_pid = None
    best_len = 0
    for key, pid in places_index.items():
        if len(key) >= 4 and len(key) > best_len:
            if _re.search(rf"\b{_re.escape(key)}\b", name):
                best_len, best_pid = len(key), pid
    return best_pid


def _place_fuzzy_match(
    name: str, places_index: Dict[str, str], threshold: float = 0.88
) -> Optional[str]:
    """Best SequenceMatcher match at or above a conservative threshold, else None."""
    from difflib import SequenceMatcher as _SM

    best_pid = None
    best_ratio = 0.0
    for key, pid in places_index.items():
        ratio = _SM(None, name, key).ratio()
        if ratio > best_ratio:
            best_ratio, best_pid = ratio, pid
    return best_pid if best_ratio >= threshold else None


def _resolve_mention_place(
    mention: Dict[str, Any],
    eq: EquipmentExtraction,
    sub_event: Dict[str, Any],
    places_index: Optional[Dict[str, str]],
) -> None:
    """A mention is ONE assertion about ONE place. Resolve the mention's OWN stated
    place_name to a single PlaceID (authoritative — what the source said); only when no
    place_name was stated, fall back to the sub-event's place if it is a single one."""
    if eq.place_name:
        mention["place_name"] = eq.place_name
        place_id = _resolve_place_id(eq.place_name, places_index)
        if place_id:
            mention["PlaceID"] = place_id
        return
    sub_places = sub_event.get("places") or []
    if len(sub_places) == 1:
        mention["PlaceID"] = sub_places[0]


def _build_equipment_data(eq: EquipmentExtraction) -> Dict[str, Any]:
    """Build equipment data dict from extraction."""
    equipment_data = {
        "common_name": eq.common_name,
        "technical_identifier": eq.technical_identifier or eq.common_name,
        "category": eq.category,
    }

    # Add optional fields
    if eq.description:
        equipment_data["description"] = eq.description
    if eq.alternate_names:
        equipment_data["alternate_names"] = list(eq.alternate_names)  # type: ignore[assignment]
    if eq.subcategory:
        equipment_data["subcategory"] = eq.subcategory
    if eq.country_of_origin:
        equipment_data["country_of_origin"] = _normalize_origin(eq.country_of_origin)
    if eq.variants:
        equipment_data["variants"] = [v.model_dump() for v in eq.variants]  # type: ignore[assignment]
    if eq.specifications:
        equipment_data["specifications"] = dict(eq.specifications)  # type: ignore[assignment]

    return equipment_data


def _recheck_equipment_fields(
    equipment_data: Dict[str, Any],
    mention: Dict[str, Any],
    grok_client: Optional[GrokClient],
) -> None:
    """Source-first gap-fill of missing critical fields (country_of_origin/category/
    quantity/place) from the mention's retained original_text, before merge/dedup. The
    recheck reads event_mentions[].original_text; pass the mention context transiently so
    it doesn't leak into the merge payload. Fail-open."""
    if not grok_client:
        return
    try:
        from src.extraction.equipment_source_recheck import (
            recheck_equipment_from_source,
        )

        injected = "event_mentions" not in equipment_data
        if injected:
            equipment_data["event_mentions"] = [mention]
        recheck_equipment_from_source(equipment_data, grok_client)
        if injected:
            del equipment_data["event_mentions"]
    except Exception as e:  # noqa: BLE001 - never block extraction
        logger.debug("equipment source-recheck skipped: %s", e)


def _process_equipment_item(
    eq_data: Dict[str, Any],
    event_data: Dict[str, Any],
    people_index: Dict[str, str],
    people_groups_index: Dict[str, str],
    dates_index: Dict[str, Dict[str, str]],
    output_root: Path,
    output_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: GrokClient,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
    places_index: Optional[Dict[str, str]] = None,
) -> Optional[Path]:
    """Process a single equipment item. Returns equipment file path or None."""
    try:
        # Validate and parse
        eq = EquipmentExtraction.model_validate(eq_data)
    except Exception as e:
        logger.warning("  Skipping invalid equipment data: %s", e)
        logger.debug("  Data: %s", eq_data)
        return None

    # ASSERTION GATE (code-enforced, not just prompt): a mention exists only when the
    # source ASSERTS presence. Drop items with no assertion_source (ambient/stock footage
    # that merely shows/discusses a place without asserting this equipment was there).
    if not eq.assertion_source:
        logger.debug(
            "  Skipping equipment with no asserted presence: %s", eq.common_name
        )
        return None

    # Link entities
    using_unit = _link_entity(eq.using_unit_name, people_groups_index, "unit")
    using_person = _link_entity(eq.using_person_name, people_index, "person")
    performance_notes = _build_performance_notes(eq)
    supporting_units = _link_supporting_units(
        eq.supporting_units, people_groups_index, eq.category, equipment_index
    )

    # Build mention and equipment data
    mention = _build_mention(
        eq,
        event_data,
        using_unit,
        using_person,
        performance_notes,
        supporting_units,
        dates_index,
        output_root,
        places_index,
    )
    equipment_data = _build_equipment_data(eq)

    # Resolve canonical identity BEFORE matching so it can serve as the merge key (lets
    # the disambiguator actually prevent cross-naming splits: M4 vs Sherman V -> one
    # record). Stamps canonical_name/identity_source/country_of_origin on equipment_data.
    if grok_client:
        _resolve_canonical_identity(equipment_data, grok_client)

    # SOURCE-RECHECK: recover missing critical fields (esp. country_of_origin — the dedup
    # veto — + category/quantity/place) from the retained original_text BEFORE merge/dedup.
    _recheck_equipment_fields(equipment_data, mention, grok_client)

    # Record-level related_equipment (narrative-sourced). Resolve/auto-create distinct
    # related records so links carry a real EquipmentID.
    if eq.related_equipment:
        equipment_data["related_equipment"] = _link_related_equipment(
            eq.related_equipment,
            equipment_index,
            output_dir,
            grok_client,
            verify_media_with_vision,
        )

    # Record-level crew_accounts (narrative-sourced; original_text mandatory; person-linked)
    if eq.crew_accounts:
        linked_accounts = _link_crew_accounts(eq.crew_accounts, people_index)
        if linked_accounts:
            equipment_data["crew_accounts"] = linked_accounts

    # Record-level environmental_performance (condition-linked; original_text mandatory)
    if eq.environmental_performance:
        linked_env = _link_environmental_performance(eq.environmental_performance)
        if linked_env:
            equipment_data["environmental_performance"] = linked_env

    # Merge or create
    try:
        eq_file = merge_or_create_equipment(
            equipment_data,
            mention,
            output_dir,
            equipment_index,
            grok_client,
            enable_enrichment,
            verify_media_with_vision,
            dates_index,
        )
        logger.debug("Updated equipment file: %s", eq_file.name)
        return eq_file
    except Exception as e:
        logger.error("Failed to save equipment '%s': %s", eq.common_name, e)
        return None


def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]:
    """Load and validate event data from file."""
    try:
        with open(event_file, encoding="utf-8") as f:
            event_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load event file {event_file}: %s", e)
        return None

    if not _validate_event_data(event_data, event_file):
        return None

    return event_data


def _finalize_extraction(
    output_dir: Path,
    modified_files: List[Path],
    event_file: Path,
    processed: Dict[str, bool],
) -> None:
    """Generate index and mark event as processed."""
    if modified_files:
        try:
            generate_equipment_index(output_dir)
        except Exception as e:
            logger.warning("Failed to generate index: %s", e)

    processed[str(event_file)] = True
    _save_processed_registry(output_dir, processed)


def _link_supporting_units(
    supporting_units_in: List["SupportingUnitInput"],
    people_groups_index: Dict[str, str],
    equipment_category: str,
    equipment_index: Optional[Dict[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Link supporting units to IDs.

    - support_type is the SUPPORTING unit's OWN arm (e.g. a P-47 wing supporting a tank
      is 'aircraft'), NOT the parent equipment's category. Falls back to the parent
      category only if the supporting unit's type is unknown.
    - Resolves PeopleGroupID by unit_name and EquipmentID by equipment_name.
    """
    linked: List[Dict[str, Any]] = []
    for su in supporting_units_in:
        support: Dict[str, Any] = {
            "support_type": su.support_type or equipment_category,
        }
        if su.unit_name:
            support["unit_name"] = su.unit_name
            group_id = people_groups_index.get(su.unit_name)
            if group_id:
                support["PeopleGroupID"] = group_id
            else:
                logger.debug("Supporting unit not found: %s", su.unit_name)
        if su.equipment_name:
            support["equipment_name"] = su.equipment_name
            eq_id = _resolve_support_equipment_id(su.equipment_name, equipment_index)
            if eq_id:
                support["EquipmentID"] = eq_id
        linked.append(support)
    return linked


def _resolve_support_equipment_id(
    name: str, equipment_index: Optional[Dict[str, Path]]
) -> Optional[str]:
    """Resolve a supporting unit's equipment name to an EquipmentID. Mirrors the main
    match path (not just exact): exact → curated-alias canonical → fuzzy, so a nickname or
    variant ("Jug", "Thunderbolt", "P-47D") links to the canonical P-47 record."""
    if not equipment_index:
        return None
    # exact
    path = equipment_index.get(name)
    # curated-alias canonical (sherman -> m4 sherman); match case-insensitively
    if not path:
        canonical = _equipment_aliases().get(_normalize_designation(name))
        if canonical:
            lower = {k.lower(): v for k, v in equipment_index.items()}
            path = lower.get(canonical.lower())
    # fuzzy (reuse the main matcher), then map matched key -> path
    if not path:
        matched = _fuzzy_match_equipment(name, equipment_index)
        if matched:
            path = equipment_index.get(matched)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("EquipmentID")
    except Exception:  # nosec B110 - best-effort link
        return None


def _link_crew_accounts(
    crew_in: List["CrewAccountInput"], people_index: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Build record-level crew_accounts (narrative-sourced). Require original_text (source
    tracking is mandatory — drop accounts without it). Resolve person_name -> PersonID via
    the people index (exact then fuzzy)."""
    linked: List[Dict[str, Any]] = []
    for acc in crew_in:
        if not acc.original_text:
            logger.debug("Dropping crew account without original_text (untraceable)")
            continue
        entry: Dict[str, Any] = {
            "person_name": acc.person_name,
            "role": acc.role,
            "observations": acc.observations,
            "original_text": acc.original_text,
            "book": acc.book,
        }
        if acc.person_name:
            pid = _resolve_person_id(acc.person_name, people_index)
            if pid:
                entry["PersonID"] = pid
        linked.append(entry)
    return linked


def _link_environmental_performance(
    env_in: List["EnvironmentalPerformanceInput"],
) -> List[Dict[str, Any]]:
    """Build record-level environmental_performance (condition-linked, narrative-sourced).
    original_text is mandatory — drop untraceable entries."""
    linked: List[Dict[str, Any]] = []
    for ep in env_in:
        if not ep.original_text:
            logger.debug("Dropping environmental_performance without original_text")
            continue
        linked.append(
            {
                "condition": ep.condition,
                "effect": ep.effect,
                "original_text": ep.original_text,
            }
        )
    return linked


def _resolve_person_id(
    name: str, people_index: Dict[str, str], threshold: float = 0.88
) -> Optional[str]:
    """Resolve a person name to a PersonID: exact (case-insensitive) then conservative
    fuzzy. The people index is keyed on the raw name, so compare case-insensitively."""
    key = name.strip().lower()
    lower_index = {k.lower(): v for k, v in people_index.items()}
    if key in lower_index:
        return lower_index[key]
    best, best_ratio = None, 0.0
    for pname, pid in lower_index.items():
        ratio = SequenceMatcher(None, key, pname).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, pid
    return best if best_ratio >= threshold else None


def _link_related_equipment(
    related_in: List["RelatedEquipmentInput"],
    equipment_index: Dict[str, Path],
    output_dir: Path,
    grok_client: Optional[GrokClient] = None,
    verify_media_with_vision: bool = True,
) -> List[Dict[str, Any]]:
    """Build the record-level related_equipment list. Each entry is narrative-sourced
    (original_text retained). Resolve name->EquipmentID; auto-create a minimal distinct
    record when the related piece has no record yet (minimal schema = EquipmentID +
    common_name). Never called for inline sub-designations (prompt keeps those out)."""
    linked: List[Dict[str, Any]] = []
    for rel in related_in:
        if not rel.name:
            continue
        entry: Dict[str, Any] = {
            "relationship": rel.relationship,
            "name": rel.name,
            "basis": rel.basis,
            "original_text": rel.original_text,
        }
        eq_id = _resolve_support_equipment_id(rel.name, equipment_index)
        if not eq_id:
            # Auto-create a minimal distinct record so the link resolves to a real ID.
            eq_id = _autocreate_minimal_equipment(
                rel.name,
                equipment_index,
                output_dir,
                grok_client,
                verify_media_with_vision,
            )
        if eq_id:
            entry["EquipmentID"] = eq_id
        linked.append(entry)
    return linked


def _autocreate_minimal_equipment(
    name: str,
    equipment_index: Dict[str, Path],
    output_dir: Path,
    grok_client: Optional[GrokClient] = None,
    verify_media_with_vision: bool = True,
) -> Optional[str]:
    """Create a minimal equipment record (EquipmentID + common_name) for a distinct piece
    the narrative relates but that has no record yet. Because the stub has no mention of
    its own, enrichment is its ONLY source of substance — so if the name is a specific
    identity (e.g. 'M26 Pershing') it is enriched on creation (Grokipedia/Wikipedia +
    canonical reference image). Registers it in the index. Returns the EquipmentID."""
    try:
        equipment_id = str(ulid.new())
        record: Dict[str, Any] = {
            "EquipmentID": equipment_id,
            "common_name": name,
            "extracted_date": datetime.now(timezone.utc).isoformat(),
            "event_mentions": [],
        }
        # Enrich the stub on identity (no Sub_eventID -> no date context).
        _enrich_on_identity(record, grok_client, verify_media_with_vision, None, None)
        safe_name = name.replace(" ", "_").replace("/", "_")
        eq_file = output_dir / f"{safe_name}_{equipment_id[:8]}.json"
        with open(eq_file, "w") as f:
            json.dump(record, f, indent=2)
        equipment_index[name] = eq_file
        logger.info("  ＋ Auto-created related equipment record: %s", name)
        return equipment_id
    except Exception as e:  # noqa: BLE001 - best-effort; link falls back to name-only
        logger.warning("Could not auto-create related equipment '%s': %s", name, e)
        return None


def _load_processed_registry(output_dir: Path) -> Dict[str, bool]:
    """Load processed events registry."""
    processed_registry = output_dir / ".processed_events.json"
    if processed_registry.exists():
        with open(processed_registry, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_processed_registry(output_dir: Path, processed: Dict[str, bool]) -> None:
    """Save processed events registry."""
    processed_registry = output_dir / ".processed_events.json"
    with open(processed_registry, "w") as f:
        json.dump(processed, f, indent=2)


def _link_entity(
    entity_name: Optional[str], entity_index: Dict[str, str], entity_type: str
) -> Optional[Dict[str, str]]:
    """Link entity name -> ID conservatively. Exact → case-insensitive → UNAMBIGUOUS
    whole-word containment → bounded fuzzy (`SequenceMatcher ≥ 0.88`). A name that could
    match several index entries (e.g. a bare "Smith" with multiple Smiths) is AMBIGUOUS and
    returns no link — never silently grab the wrong person/unit. (Previously an unbounded
    substring match could link "Sergeant Smith" to any "Smith".)"""
    if not entity_name or not entity_index:
        return None
    id_key = "PersonID" if entity_type == "person" else "PeopleGroupID"

    # 1. exact
    entity_id = entity_index.get(entity_name)
    if entity_id:
        return {id_key: entity_id, "name": entity_name}

    name_lower = entity_name.lower().strip()

    # 2. case-insensitive exact
    for idx_name, idx_id in entity_index.items():
        if idx_name.lower() == name_lower:
            return {id_key: idx_id, "name": idx_name}

    # 3. unambiguous whole-word containment, then 4. bounded fuzzy
    return _link_contained(
        name_lower, entity_index, id_key, entity_type
    ) or _link_fuzzy(name_lower, entity_index, id_key)


def _link_contained(name_lower, entity_index, id_key, entity_type):
    """Link only if exactly ONE index entry contains the stated name as a whole phrase
    (ambiguous -> no link). Returns a link dict, None (fall through to fuzzy), or signals
    ambiguity by returning None after logging."""
    import re as _re

    if len(name_lower) < 4:
        return None
    contained = [
        (n, i)
        for n, i in entity_index.items()
        if _re.search(rf"\b{_re.escape(name_lower)}\b", n.lower())
    ]
    if len(contained) == 1:
        return {id_key: contained[0][1], "name": contained[0][0]}
    if len(contained) > 1:
        logger.debug(
            "%s '%s' ambiguous (%d candidates) — no link",
            entity_type,
            name_lower,
            len(contained),
        )
    return None


def _link_fuzzy(name_lower, entity_index, id_key, threshold: float = 0.88):
    """Bounded fuzzy match (reject distant). Returns a link dict or None."""
    best, best_ratio = None, 0.0
    for idx_name, idx_id in entity_index.items():
        ratio = SequenceMatcher(None, name_lower, idx_name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, (idx_name, idx_id)
    if best and best_ratio >= threshold:
        return {id_key: best[1], "name": best[0]}
    return None


def _build_performance_notes(eq: EquipmentExtraction) -> Optional[Dict[str, List[str]]]:
    """Build performance notes from equipment data."""
    if not any(
        [
            eq.performance_successes,
            eq.performance_failures,
            eq.field_modifications,
            eq.maintenance_issues,
        ]
    ):
        return None
    return {
        "successes": eq.performance_successes,
        "failures": eq.performance_failures,
        "field_modifications": eq.field_modifications,
        "maintenance_issues": eq.maintenance_issues,
    }


def _add_metadata_to_mention(
    mention: Dict[str, Any], event_data: Dict[str, Any]
) -> None:
    """Add book metadata to mention."""
    metadata = event_data.get("metadata", {})
    if metadata.get("book_title"):
        mention["book"] = metadata["book_title"]
    if metadata.get("author"):
        mention["author"] = metadata["author"]
    if metadata.get("series"):
        mention["series"] = metadata["series"]
    if metadata.get("chapter_title"):
        mention["chapter"] = metadata["chapter_title"]


def _add_event_names_to_mention(
    mention: Dict[str, Any], event_data: Dict[str, Any]
) -> None:
    """Add event names to mention."""
    if event_data["Event"].get("Event_Name"):
        mention["Event_Name"] = event_data["Event"]["Event_Name"]
    # Find the sub-event matching the mention's Sub_eventID
    sub_event_id = mention.get("Sub_eventID")
    for se in event_data["Event"].get("Sub-events", []):
        if se.get("Sub-eventID") == sub_event_id and se.get("Sub-event_Name"):
            mention["Sub_event_Name"] = se["Sub-event_Name"]
            break


def _link_date_to_mention(
    mention: Dict[str, Any], dates_index: Dict[str, Dict[str, str]], output_root: Path
) -> None:
    """Link date to mention."""
    event_id = mention["EventID"]
    sub_event_id = mention["Sub_eventID"]
    date_key = f"{event_id}:{sub_event_id}"
    if date_key not in dates_index:
        return

    mention["DateID"] = dates_index[date_key]["DateID"]
    mention["DateMentionID"] = dates_index[date_key]["DateMentionID"]

    # Add human-readable date
    date_file = output_root / "dates" / f"{dates_index[date_key]['DateID']}.json"
    if date_file.exists():
        with open(date_file, encoding="utf-8") as f:
            date_data = json.load(f)
            date_val = date_data.get("date_start") or date_data.get("date")
            if date_val:
                mention["date"] = date_val
    logger.debug("Linked to date %s", mention["DateID"])


def extract_equipment_from_event(
    event_file: Path,
    output_dir: Path,
    grok_client: GrokClient,
    output_root: Optional[Path] = None,
    max_retries: int = 3,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
) -> List[Path]:
    """Extract equipment from event file.

    Args:
        event_file: Path to event JSON file
        output_dir: Output directory for equipment files
        grok_client: Grok API client
        output_root: Root output directory (defaults to output_dir.parent)
        max_retries: Maximum retry attempts per extraction
        enable_enrichment: Enable external data enrichment (Wikipedia/Grokipedia)
        verify_media_with_vision: Verify media relevance with Grok vision API

    Returns:
        List of created/updated equipment file paths
    """
    logger.info("Extracting equipment from %s", event_file)

    # Setup
    output_dir.mkdir(parents=True, exist_ok=True)
    processed = _load_processed_registry(output_dir)
    if str(event_file) in processed:
        from src.utils.config import should_reprocess

        if not should_reprocess("equipment"):
            logger.debug("  Already processed, skipping")
            return []

    # Load and validate event data
    event_data = _load_event_data(event_file)
    if not event_data:
        return []

    # Load indices
    output_root = output_root or output_dir.parent
    try:
        people_index, people_groups_index, dates_index = load_entity_indices(
            output_root
        )
        equipment_index = load_equipment_index(output_dir)
        # Places name -> PlaceID index. Use the places module's OWN alias-aware index
        # (keys on current_name AND every alias) rather than a bare 'name' lookup, so a
        # mention's place_name resolves even when the source uses an alias/variant. Falls
        # back to the generic index if the places module is unavailable.
        try:
            from src.extraction.places import _build_place_name_index

            places_index, _ = _build_place_name_index(output_root / "places")
        except Exception:  # noqa: BLE001 - fall back to the generic name index
            from src.utils.entity_index import build_name_index

            places_index = build_name_index(
                output_root / "places", "PlaceID", "current_name"
            )
    except Exception as e:
        logger.error("Failed to load indices: %s", e)
        return []

    # Extract equipment using LLM
    equipment_list = _extract_equipment_with_llm(event_data, grok_client, max_retries)
    if not equipment_list:
        logger.info("  No equipment extracted")
        return []

    # Process all equipment items
    modified_files = [
        eq_file
        for eq_data in equipment_list
        if (
            eq_file := _process_equipment_item(
                eq_data,
                event_data,
                people_index,
                people_groups_index,
                dates_index,
                output_root,
                output_dir,
                equipment_index,
                grok_client,
                enable_enrichment,
                verify_media_with_vision,
                places_index,
            )
        )
    ]

    # Finalize
    _finalize_extraction(output_dir, modified_files, event_file, processed)
    return modified_files


def generate_equipment_index(equipment_dir: Path) -> None:
    """Generate index.json mapping equipment names to files."""
    index = {}

    for eq_file in equipment_dir.glob("*.json"):
        if eq_file.name in ("index.json", ".processed_events.json"):
            continue
        try:
            with open(eq_file, encoding="utf-8") as file_handle:
                eq_data = json.load(file_handle)
                index[eq_data["common_name"]] = eq_file.name
        except Exception as e:
            logger.warning("Failed to index %s: %s", eq_file.name, e)

    index_file = equipment_dir / "index.json"
    with open(index_file, "w", encoding="utf-8") as file_handle:
        json.dump(index, file_handle, indent=2, sort_keys=True)

    logger.info("Generated index with %d equipment entries", len(index))


if __name__ == "__main__":
    import sys
    from src.grok_client import GrokClient  # pylint: disable=reimported

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m src.extraction.equipment <event_file>")
        sys.exit(1)

    event_file = Path(sys.argv[1])
    output_dir = Path("output/equipment")
    output_root = Path("output")
    cache_dir = Path("cache/api")

    grok = GrokClient(cache_dir)

    files = extract_equipment_from_event(
        event_file,
        output_dir,
        grok,
        output_root,
    )

    print(f"\nCreated {len(files)} equipment files:")
    for file_path in files:
        print(f"  {file_path}")
