"""People extraction from event data."""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import ulid
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.grok_client import GrokClient
from src.utils.json_validator import _fix_invalid_ulids
from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)

# Compiled regex patterns for performance
_SPECIAL_CHARS_PATTERN = re.compile(r"[^\w\s-]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


# Rank abbreviation normalization
RANK_ABBREVIATIONS = {
    "Gen.": "General",
    "Lt. Gen.": "Lieutenant General",
    "Maj. Gen.": "Major General",
    "Brig. Gen.": "Brigadier General",
    "Col.": "Colonel",
    "Lt. Col.": "Lieutenant Colonel",
    "Maj.": "Major",
    "Capt.": "Captain",
    "Lt.": "Lieutenant",
    "1st Lt.": "First Lieutenant",
    "2nd Lt.": "Second Lieutenant",
    "Sgt.": "Sergeant",
    "Cpl.": "Corporal",
    "Pvt.": "Private",
    "Adm.": "Admiral",
    "Vice Adm.": "Vice Admiral",
    "Rear Adm.": "Rear Admiral",
    "Cmdr.": "Commander",
    "Lt. Cmdr.": "Lieutenant Commander",
}


@lru_cache(maxsize=1000)
def _normalize_rank(rank: str) -> str:
    """Normalize rank abbreviations to full names."""
    return RANK_ABBREVIATIONS.get(rank, rank)


@lru_cache(maxsize=500)
def _normalize_branch(branch: str) -> str:
    """Normalize branch names."""
    branch_map = {
        "US Army": "U.S. Army",
        "U.S Army": "U.S. Army",
        "US Navy": "U.S. Navy",
        "U.S Navy": "U.S. Navy",
        "US Air Force": "U.S. Air Force",
        "U.S Air Force": "U.S. Air Force",
        "US Marine Corps": "U.S. Marine Corps",
        "U.S Marine Corps": "U.S. Marine Corps",
    }
    return branch_map.get(branch, branch)


@lru_cache(maxsize=1000)
def _normalize_unit(unit: str) -> str:
    """Normalize unit names."""
    unit_map = {
        "OPD, War Department": "Operations Division (OPD), War Department",
        "OPD": "Operations Division (OPD), War Department",
        "Operations Division": "Operations Division (OPD), War Department",
    }
    return unit_map.get(unit, unit)


def _deduplicate_units(units: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate and normalize units.

    Priority: Unit with dates > Unit with partial dates > Unit no dates
    """
    if not units:
        return []

    # Normalize all units
    normalized = []
    for u in units:
        norm_unit = _normalize_unit(u.get("unit", ""))
        normalized.append(
            {
                "unit": norm_unit,
                "from": u.get("from"),
                "to": u.get("to"),
            }
        )

    # Group by unit name
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for u in normalized:
        key = u["unit"]
        if key not in groups:
            groups[key] = []
        groups[key].append(u)

    # For each group, keep best entry
    result = []
    for entries in groups.values():
        # Sort by priority: both dates > one date > no dates
        def priority(x):
            has_from = x["from"] is not None
            has_to = x["to"] is not None
            return (
                has_from and has_to,
                has_from or has_to,
                x["from"] or "",
                x["to"] or "",
            )

        best = max(entries, key=priority)
        result.append(best)

    return result


def _deduplicate_awards(awards: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate military awards.

    Priority: Award with full date > Award with partial date > Award no date
    """
    if not awards:
        return []

    # Group by (award, class)
    groups: Dict[tuple[str, Optional[str]], list[Dict[str, Any]]] = {}
    for a in awards:
        key = (a.get("award", ""), a.get("class"))
        if key not in groups:
            groups[key] = []
        groups[key].append(a)

    # For each group, keep best entry (most specific date)
    result = []
    for entries in groups.values():
        # Sort by date specificity: full date (YYYY-MM-DD) > year only > no date
        def date_priority(x):
            date = x.get("date_awarded")
            if not date:
                return (0, "")
            # Full date has dashes, year only doesn't
            has_full_date = "-" in date
            return (2 if has_full_date else 1, date)

        best = max(entries, key=date_priority)
        result.append(best)

    return result


def _deduplicate_ranks(ranks: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate and normalize ranks.

    Priority: Rank with date > Abbreviated with date > Full no date > Abbreviated no date
    """
    if not ranks:
        return []

    # Normalize all ranks
    normalized = []
    for r in ranks:
        norm_rank = _normalize_rank(r.get("rank", ""))
        norm_branch = (
            _normalize_branch(r.get("branch", "")) if r.get("branch") else None
        )
        normalized.append(
            {
                "rank": norm_rank,
                "date": r.get("date"),
                "branch": norm_branch,
            }
        )

    # Group by (rank, branch)
    groups: Dict[tuple[str, Optional[str]], list[Dict[str, Any]]] = {}
    for r in normalized:
        key = (r["rank"], r["branch"])
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    # For each group, keep best entry
    result = []
    for entries in groups.values():
        # Sort by priority: has date (True first), then by date if both have dates
        best = max(entries, key=lambda x: (x["date"] is not None, x["date"] or ""))
        result.append(best)

    return result


class MilitaryAward(BaseModel):
    """Military award or decoration received by a person."""

    award: str
    class_: Optional[str] = Field(default=None, alias="class")
    date_awarded: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class MilitaryRank(BaseModel):
    """Military rank held by a person."""

    rank: str
    date: Optional[str] = None
    branch: Optional[str] = None


class UnitServed(BaseModel):
    """Military unit in which a person served."""

    unit: str
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class Education(BaseModel):
    """Educational institution attended by a person."""

    institution: str
    degree: Optional[str] = None
    year: Optional[str] = None


class Family(BaseModel):
    """Family information for a person."""

    spouse: Optional[str] = None
    children: list[str] = Field(default_factory=list)


class BiographySource(BaseModel):
    """Source of biographical information."""

    source: str
    page: Optional[str] = None  # Can be single page or range (e.g., "16" or "16-17")
    confidence: Optional[float] = None
    fields_sourced: list[str] = Field(
        default_factory=list,
        description="List of biographical fields sourced from this reference",
    )

    @field_validator("page", mode="before")
    @classmethod
    def convert_page_to_string(cls, v):
        """Convert page to string if it's an int."""
        if v is None:
            return None
        return str(v)


class BiographicalProfile(BaseModel):
    """Biographical profile information for a person."""

    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    death_date: Optional[str] = None
    death_place: Optional[str] = None
    nationality: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-3 country code (e.g., 'USA', 'GBR', 'DEU', 'FRA', 'CAN')",
    )
    role_type: Optional[str] = None
    ranks: list[MilitaryRank] = Field(default_factory=list)
    units_served: list[UnitServed] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    military_awards: list[MilitaryAward] = Field(default_factory=list)
    family: Optional[Family] = None
    aliases: list[str] = Field(default_factory=list)
    biography_sources: list[BiographySource] = Field(default_factory=list)
    biographical_details: Optional[str] = None


class PersonEventMention(BaseModel):
    """Event mention linking a person to a specific event."""

    MentionID: str = Field(description="26-character ULID")
    Event_Name: str
    EventID: str = Field(description="26-character ULID")
    Sub_event_Name: str = Field(alias="Sub-event_Name")
    Sub_eventID: str = Field(description="26-character ULID", alias="Sub-eventID")
    book: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    date: Optional[str] = None
    DateMentionID: Optional[str] = None
    position_at_event: Optional[str] = None
    life_event: Optional[str] = None
    original_text: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class Person(BaseModel):
    """Person entity with biographical information and event mentions."""

    PersonID: str = Field(description="26-character ULID")
    name: str
    source_language: str = "English"
    biographical_profile: Optional[BiographicalProfile] = None
    event_mentions: list[PersonEventMention] = Field(default_factory=list)


class PeopleOutput(BaseModel):
    """Output container for extracted people entities."""

    People: list[Person]


SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract all people mentions with biographical details and event context.
Return structured data matching the schema."""


def create_people_prompt(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    book: str = "",
    author: str = "",
    series: str = "",
) -> str:
    """Create prompt for people extraction from a sub-event."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text_parts = []
    for key in sorted(fulltext.keys()):
        text_parts.append(fulltext[key])
    text = "\n\n".join(text_parts)

    prompt = f"""Extract all people mentions from this WWII text with biographical details.

Source: {book} by {author} ({series})
Event: {event_name} (ID: {event_id})
Sub-event: {sub_event_summary} (ID: {sub_event_id})

Text:
{text}

Return JSON matching this structure:
{{
  "People": [
    {{
      "PersonID": "01H8XYZI1AB123CD456EF789GH",
      "name": "Dwight D. Eisenhower",
      "source_language": "English",
      "biographical_profile": {{
        "birth_date": "1890-10-14",
        "birth_place": "Denison, Texas, USA",
        "death_date": "1969-03-28",
        "death_place": "Washington, D.C., USA",
        "nationality": "USA",
        "role_type": "military_leader",
        "ranks": [
          {{"rank": "General", "date": "1943", "branch": "US Army"}},
          {{"rank": "General of the Army", "date": "1944-12-20", "branch": "US Army"}}
        ],
        "units_served": [
          {{"unit": "Supreme Headquarters Allied Expeditionary Force", "from": "1943", "to": "1945"}}
        ],
        "education": [
          {{"institution": "United States Military Academy", "degree": "Bachelor of Science", "year": "1915"}}
        ],
        "military_awards": [
          {{"award": "Distinguished Service Medal", "class": null, "date_awarded": "1945-05-08"}}
        ],
        "family": {{"spouse": "Mamie Eisenhower", "children": ["Doud Dwight Eisenhower", "John Eisenhower"]}},
        "aliases": ["Ike"],
        "biography_sources": [
          {{
            "source": "{book}",
            "page": null,
            "confidence": 0.9,
            "fields_sourced": ["birth_date", "nationality", "ranks", "biographical_details"]
          }}
        ],
        "biographical_details": "Supreme Commander of Allied Forces in Europe"
      }},
      "event_mentions": [
        {{
          "MentionID": "01H8XYZJ2MN456PQ789RS012TU",
          "Event_Name": "{event_name}",
          "EventID": "{event_id}",
          "Sub-event_Name": "{sub_event_summary}",
          "Sub-eventID": "{sub_event_id}",
          "book": "{book}",
          "author": "{author}",
          "series": "{series}",
          "date": null,
          "DateMentionID": null,
          "position_at_event": "Supreme Commander",
          "life_event": "Directed Allied operations",
          "original_text": "General Eisenhower ordered the attack"
        }}
      ]
    }}
  ]
}}

Extract biographical data when mentioned:
- ranks: Military rank progression with dates
- units_served: Military units with service periods
- education: Educational institutions and degrees
- military_awards: Medals and decorations
- family: Spouse and children names
- aliases: Nicknames, alternative names, titles
- biography_sources: Track which fields came from this source

For biography_sources, include:
- source: Book title (use "{book}")
- page: Page number if mentioned
- confidence: 0.0-1.0 (how certain the extraction is)
- fields_sourced: List of field names extracted from this source

Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
If no people found, return empty People array."""

    return prompt


@lru_cache(maxsize=5000)
@lru_cache(maxsize=1000)
def _normalize_name(name: str) -> str:
    """Normalize person name for matching (deprecated - use text_utils.normalize_name)."""
    return normalize_name(name)


def _name_to_filename(name: str, person_id: str) -> str:
    """Convert person name to safe filename."""
    # Remove special characters, keep alphanumeric and spaces
    safe_name = _SPECIAL_CHARS_PATTERN.sub("", name)
    # Replace spaces with underscores
    safe_name = _WHITESPACE_PATTERN.sub("_", safe_name)
    # Limit length and add ULID suffix for uniqueness
    safe_name = safe_name[:50]
    return f"{safe_name}_{person_id[:12]}.json"


def _load_person_file(person_file: Path) -> Dict[str, Any]:
    """Load a person file."""
    with open(person_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_person_file(person_file: Path, person_data: Dict[str, Any]) -> None:
    """Save a person file."""
    with open(person_file, "w", encoding="utf-8") as f:
        json.dump(person_data, f, indent=2, ensure_ascii=False)


def _update_index(index_file: Path, name: str, filename: str) -> None:
    """Update the people index atomically."""
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    name_key = _normalize_name(name)
    index[name_key] = filename

    # Atomic write: write to temp file, then rename
    temp_file = index_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    temp_file.replace(index_file)


def _merge_list_field(existing: list, new: list, dedupe_func=None) -> list:
    """Merge list fields with optional deduplication function."""
    if dedupe_func:
        return dedupe_func(existing + new)
    # JSON-based deduplication for other fields
    item_set = {json.dumps(item, sort_keys=True) for item in existing}
    for item in new:
        item_json = json.dumps(item, sort_keys=True)
        if item_json not in item_set:
            existing.append(item)
    return existing


def _merge_family(
    existing_family: Dict[str, Any], new_family: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge family information."""
    if not existing_family:
        return new_family
    if not new_family:
        return existing_family

    if not existing_family.get("spouse") and new_family.get("spouse"):
        existing_family["spouse"] = new_family["spouse"]

    existing_children = existing_family.get("children", [])
    new_children = new_family.get("children", [])
    child_set = set(existing_children)
    for child in new_children:
        if child not in child_set:
            existing_children.append(child)
    existing_family["children"] = existing_children
    return existing_family


def _update_missing_fields(
    existing_bio: Dict[str, Any], new_bio: Dict[str, Any]
) -> None:
    """Update biographical fields if new data exists and old doesn't."""
    for field in [
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "nationality",
        "role_type",
        "biographical_details",
    ]:
        if new_bio.get(field) and not existing_bio.get(field):
            existing_bio[field] = new_bio[field]


def _merge_person(
    existing: Dict[str, Any], new_person: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge new person data into existing person record."""
    # Add new event mentions
    existing_mentions = existing.get("event_mentions", [])
    new_mentions = new_person.get("event_mentions", [])
    existing_mentions.extend(new_mentions)
    existing["event_mentions"] = existing_mentions

    # Merge biographical profile if new data is more complete
    new_bio = new_person.get("biographical_profile")
    existing_bio = existing.get("biographical_profile")

    if new_bio and existing_bio:
        # Merge with deduplication
        existing_bio["military_awards"] = _merge_list_field(
            existing_bio.get("military_awards", []),
            new_bio.get("military_awards", []),
            _deduplicate_awards,
        )
        existing_bio["ranks"] = _merge_list_field(
            existing_bio.get("ranks", []), new_bio.get("ranks", []), _deduplicate_ranks
        )
        existing_bio["units_served"] = _merge_list_field(
            existing_bio.get("units_served", []),
            new_bio.get("units_served", []),
            _deduplicate_units,
        )
        existing_bio["education"] = _merge_list_field(
            existing_bio.get("education", []), new_bio.get("education", [])
        )
        existing_bio["aliases"] = _merge_list_field(
            existing_bio.get("aliases", []), new_bio.get("aliases", [])
        )
        existing_bio["biography_sources"] = _merge_list_field(
            existing_bio.get("biography_sources", []),
            new_bio.get("biography_sources", []),
        )

        # Merge family
        new_family = new_bio.get("family")
        if new_family:
            existing_family = existing_bio.get("family", {})
            existing_bio["family"] = _merge_family(existing_family, new_family)

        # Update missing fields
        _update_missing_fields(existing_bio, new_bio)
    elif new_bio:
        existing["biographical_profile"] = new_bio

    return existing


def _check_if_processed(event_file: Path, people_dir: Path) -> bool:
    """Check if event file has already been processed. Returns True if processed."""
    processed_registry = people_dir / ".processed_events.json"
    event_file_str = str(event_file.resolve())

    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
        if event_file_str in processed:
            logger.info(
                f"Event already processed for people extraction: {event_file.name}"
            )
            return True

    return False


def _load_book_metadata(event_file: Path) -> tuple[str, str, str]:
    """Load book metadata from parsed file. Returns (book, author, series)."""
    parsed_file = event_file.parent / event_file.name.replace(
        "-event.json", "-parsed.json"
    )

    if not parsed_file.exists():
        return "", "", ""

    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        if not parsed_data:
            return "", "", ""

        return (
            parsed_data.get("book", ""),
            parsed_data.get("author", ""),
            parsed_data.get("series", ""),
        )


def _load_people_index(index_file: Path) -> dict:
    """Load people index from file."""
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_people_index(index_file: Path, index: dict) -> None:
    """Save people index atomically."""
    temp_file = index_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    temp_file.replace(index_file)


def _process_person(
    person: dict,
    people_dir: Path,
    index: dict,
) -> tuple[bool, bool]:
    """Process a single person. Returns (is_new, is_updated)."""
    name = person["name"]
    person_id = person["PersonID"]
    name_key = _normalize_name(name)

    # Check in-memory index for existing person
    existing_filename = index.get(name_key)

    if existing_filename:
        # Update existing person
        person_file = people_dir / existing_filename
        if person_file.exists():
            existing_person = _load_person_file(person_file)
            merged = _merge_person(existing_person, person)
            Person(**merged)  # Validate
            _save_person_file(person_file, merged)
            logger.debug("  Updated: %s", name)
            return False, True
        else:
            # Index points to missing file, create new
            filename = _name_to_filename(name, person_id)
            person_file = people_dir / filename
            _save_person_file(person_file, person)
            index[name_key] = filename
            logger.debug("  Created: %s", name)
            return True, False
    else:
        # New person
        filename = _name_to_filename(name, person_id)
        person_file = people_dir / filename
        _save_person_file(person_file, person)
        index[name_key] = filename
        logger.debug("  Created: %s", name)
        return True, False


def _extract_people_for_sub_event(
    sub_event: dict,
    event_id: str,
    event_name: str,
    book: str,
    author: str,
    series: str,
    grok_client: GrokClient,
    max_retries: int,
) -> Optional[list]:
    """Extract people for a single sub-event with retry logic."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    logger.info("  Processing sub-event %s", sub_event_id)

    prompt = create_people_prompt(sub_event, event_id, event_name, book, author, series)

    for attempt in range(max_retries):
        try:
            people_output = grok_client.extract_structured(
                prompt=prompt,
                schema=PeopleOutput,
                system_prompt=SYSTEM_PROMPT,
                use_cache=(attempt == 0),
                cache_type="people",
            )

            people_dict: Dict[str, Any] = people_output.model_dump(by_alias=True)
            people_dict = _fix_invalid_ulids(people_dict)  # type: ignore

            extracted_people = people_dict.get("People", [])
            logger.info("  ✓ Processed %d people", len(extracted_people))
            return extracted_people

        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as e:
            if attempt < max_retries - 1:
                logger.warning(f"  ⚠ Attempt {attempt + 1} failed: {e}")
                logger.info(f"  Retrying ({attempt + 2}/{max_retries})...")
            else:
                logger.error(f"  ✗ All {max_retries} attempts failed: {e}")
                return None

    return None


def _mark_event_processed(
    event_file: Path,
    people_dir: Path,
    event_data: dict,
    new_people_count: int,
    updated_people_count: int,
) -> None:
    """Mark event file as processed in registry."""
    processed_registry = people_dir / ".processed_events.json"
    event_file_str = str(event_file.resolve())

    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
    else:
        processed = {}

    processed[event_file_str] = {
        "processed_at": event_data.get("extracted_date", ""),
        "new_people": new_people_count,
        "updated_people": updated_people_count,
    }

    with open(processed_registry, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2)


def extract_people(
    event_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract people from event file and save as individual files.

    Args:
        event_file: Path to event JSON file
        grok_client: Grok API client
        output_dir: Root output directory (contains all books)

    Returns:
        Path to people directory, or None if failed
    """
    # People directory - individual files per person
    people_dir = output_dir / "people"
    people_dir.mkdir(parents=True, exist_ok=True)

    # Check if already processed
    if _check_if_processed(event_file, people_dir):
        return people_dir

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Get book metadata
    book, author, series = _load_book_metadata(event_file)

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    new_people_count = 0
    updated_people_count = 0

    # Load index
    index_file = people_dir / "index.json"
    index = _load_people_index(index_file)

    for sub_event in sub_events:
        extracted_people = _extract_people_for_sub_event(
            sub_event,
            event_id,
            event_name,
            book,
            author,
            series,
            grok_client,
            max_retries,
        )

        if not extracted_people:
            continue

        # Process each person
        for person in extracted_people:
            is_new, is_updated = _process_person(person, people_dir, index)
            if is_new:
                new_people_count += 1
            if is_updated:
                updated_people_count += 1

    # Save index
    _save_people_index(index_file, index)

    # Mark as processed
    _mark_event_processed(
        event_file, people_dir, event_data, new_people_count, updated_people_count
    )

    logger.info(
        "People directory: %d new, %d updated", new_people_count, updated_people_count
    )

    return people_dir
