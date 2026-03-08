"""Supplemental material extraction from event data - Phase 1: Core Extraction."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import ulid
from jsonschema import ValidationError, validate

from src.grok_client import GrokClient
from src.json_schemas import SUPPLEMENTAL_SCHEMA

logger = logging.getLogger(__name__)


def _build_people_index(output_root: Path) -> Dict[str, str]:
    """Build people name -> PersonID index."""
    people_dir = output_root / "people"
    skip_files = [
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        ".processed_events.json",
    ]

    index: Dict[str, str] = {}
    if not people_dir.exists():
        return index

    for json_file in people_dir.glob("*.json"):
        if json_file.name in skip_files:
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                person_data = json.load(f)
                if "PersonID" in person_data and "name" in person_data:
                    index[person_data["name"]] = person_data["PersonID"]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)

    return index


def _build_groups_index(output_root: Path) -> Dict[str, str]:
    """Build group name -> GroupID index (includes aliases)."""
    groups_dir = output_root / "people_groups"
    skip_files = ["index.json", ".processed_events.json", "related_groups_report.json"]

    index: Dict[str, str] = {}
    if not groups_dir.exists():
        return index

    for json_file in groups_dir.glob("*.json"):
        if json_file.name in skip_files:
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                group_data = json.load(f)
                group_id = group_data.get("GroupID") or group_data.get("PeopleGroupID")
                group_name = group_data.get("group_name") or group_data.get("name")

                if group_id and group_name:
                    index[group_name] = group_id
                    # Also index aliases
                    for alias in group_data.get("aliases", []):
                        index[alias] = group_id
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)

    return index


def _resolve_author_ids(
    authors: List[str], people_index: Dict[str, str]
) -> List[Optional[str]]:
    """Resolve author names to PersonIDs."""
    return [people_index.get(name) for name in authors]


def _resolve_mentioned_people(
    citation_text: str, people_index: Dict[str, str]
) -> List[Dict[str, str]]:
    """Extract people mentioned in citation text."""
    mentioned = []
    for name, person_id in people_index.items():
        if name.lower() in citation_text.lower():
            mentioned.append({"PersonID": person_id, "name": name})
    return mentioned


def _resolve_mentioned_organizations(
    citation_text: str, groups_index: Dict[str, str]
) -> List[Dict[str, str]]:
    """Extract organizations mentioned in citation text."""
    mentioned = []
    for name, group_id in groups_index.items():
        if name.lower() in citation_text.lower():
            mentioned.append({"PeopleGroupID": group_id, "name": name})
    return mentioned


SYSTEM_PROMPT = """You are an expert librarian and historian analyzing World War II documents.
Extract all supplemental material references (endnotes, footnotes, bibliography) from the provided event text.

CRITICAL: First determine the material category:
- "referenced_material": Citations to books, articles, documents, archives (has author/title/publisher)
- "supplemental_information": Additional narrative, context, or explanations (no formal citation)

Phase 1 Requirements:
- Identify reference type (endnote, footnote, bibliography)
- Extract reference number or symbol (null for unnumbered bibliography)
- Preserve verbatim reference text exactly as it appears
- Determine material_category: "referenced_material" or "supplemental_information"
- For referenced_material, parse citation into structured components:
  * author(s) as array
  * title
  * publisher (if applicable)
  * publication_date (YYYY, YYYY-MM, or YYYY-MM-DD, or "UNKNOWN")
  * first_edition_date (for books with long publication history)
  * publication_location
  * publication_country (ISO 3166-1 alpha-3)
  * isbn (preferably first edition, null for pre-1966 books)
  * isbn_edition (if ISBN is not first edition)
  * pages, volume, edition, translator (if applicable)
  * periodical_name (for journals/periodicals)
  * document_type (e.g., "Primary source", "Journal article")
  * author_death_date (for copyright determination, if known)
- For supplemental_information, set citation fields to null/empty
- Classify availability: online, offline, archive, or unknown
- For online: extract all URLs
- For offline/archive materials:
  * archive_reference_number (document reference number, if available)
  * archive_physical_address (physical address of archive, if known)
- Initial license determination (public_domain for government/educational, copyright, or unknown)

Return ONLY valid JSON. No additional text."""


def create_supplemental_prompt(event_data: Dict[str, Any]) -> List[tuple]:
    """Create prompt for supplemental material extraction."""
    event_name = event_data.get("Event", {})
    event_id = event_name.get("EventID", "")
    event_title = event_name.get("Event_Name", "")

    prompts = []

    for sub_event in event_name.get("Sub-events", []):
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_summary = sub_event.get("Sub-event_summary", "")
        fulltext = sub_event.get("Sub-event_fulltext", {})
        endnote_refs = sub_event.get("Endnote_References", [])
        footnote_refs = sub_event.get("Footnote_References", [])

        text = "\n".join(fulltext.values())

        prompt = f"""Extract supplemental material references from this sub-event:

Event: {event_title}
EventID: {event_id}
Sub-event: {sub_event_summary}
Sub-eventID: {sub_event_id}

Text:
{text}

Endnote References Found: {endnote_refs}
Footnote References Found: {footnote_refs}

Return JSON in this format:
{{
  "Event_Name": "{event_title}",
  "EventID": "{event_id}",
  "Sub-event_Name": "{sub_event_summary}",
  "Sub-eventID": "{sub_event_id}",
  "Supplemental_Material": [
    {{
      "MaterialID": "GENERATE_NEW_ULID",
      "EventID": "{event_id}",
      "Sub-eventID": "{sub_event_id}",
      "reference_type": "endnote",
      "reference_number": "4",
      "verbatim_reference": "Shirer, William L. The Rise and Fall of the Third Reich: A History of Nazi Germany. New York: Simon & Schuster, 1960, pp. 597-598.",
      "citation": {{
        "author": ["Shirer, William L."],
        "title": "The Rise and Fall of the Third Reich: A History of Nazi Germany",
        "publisher": "Simon & Schuster",
        "publication_date": "1960",
        "first_edition_date": "1960",
        "publication_location": "New York",
        "publication_country": "USA",
        "isbn": null,
        "isbn_edition": null,
        "pages": "597-598",
        "volume": null,
        "edition": null,
        "translator": null,
        "periodical_name": null,
        "document_type": null
      }},
      "availability": "offline",
      "resource_urls": [],
      "license": "copyright",
      "license_notes": "Copyright - Simon & Schuster"
    }},
    {{
      "MaterialID": "GENERATE_NEW_ULID",
      "EventID": "{event_id}",
      "Sub-eventID": "{sub_event_id}",
      "reference_type": "endnote",
      "reference_number": "5",
      "verbatim_reference": "German Federal Archives, 'Invasion of Poland - Military Orders,' Document R 43 II/1270, September 1, 1939.",
      "citation": {{
        "author": ["German Federal Archives"],
        "title": "Invasion of Poland - Military Orders",
        "publisher": null,
        "publication_date": "1939-09-01",
        "first_edition_date": null,
        "publication_location": null,
        "publication_country": "DEU",
        "isbn": null,
        "isbn_edition": null,
        "pages": null,
        "volume": null,
        "edition": null,
        "translator": null,
        "periodical_name": null,
        "document_type": "Primary source document"
      }},
      "availability": "online",
      "resource_urls": ["https://www.bundesarchiv.de/cocoon/barch/0/r/r43ii/1270/index.html"],
      "license": "public_domain",
      "license_notes": "Historical government document"
    }},
    {{
      "MaterialID": "GENERATE_NEW_ULID",
      "EventID": "{event_id}",
      "Sub-eventID": "{sub_event_id}",
      "reference_type": "footnote",
      "reference_number": "*",
      "verbatim_reference": "The exact time of 4:45 AM is disputed. See Zaloga, Steven J. Poland 1939: The Birth of Blitzkrieg. Oxford: Osprey Publishing, 2002, p. 42.",
      "citation": {{
        "author": ["Zaloga, Steven J."],
        "title": "Poland 1939: The Birth of Blitzkrieg",
        "publisher": "Osprey Publishing",
        "publication_date": "2002",
        "first_edition_date": "2002",
        "publication_location": "Oxford",
        "publication_country": "GBR",
        "isbn": "978-1841763552",
        "isbn_edition": null,
        "pages": "42",
        "volume": null,
        "edition": null,
        "translator": null,
        "periodical_name": null,
        "document_type": null,
        "author_death_date": null
      }},
      "availability": "offline",
      "resource_urls": [],
      "archive_reference_number": null,
      "archive_physical_address": null,
      "license": "copyright",
      "license_notes": "Copyright - Osprey Publishing"
    }}
  ]
}}

Extract ALL references (endnotes, footnotes, bibliography).
For MaterialID, use placeholder "GENERATE_NEW_ULID" - will be replaced with actual ULIDs.
For online resources, include all URLs in resource_urls array.
For archive materials, include archive_reference_number and archive_physical_address if available.
For author_death_date, include if known (format: YYYY or YYYY-MM-DD).
For government/educational institutions, use license "public_domain".
For commercial publishers, use license "copyright".
If uncertain, use license "unknown".
Use ISO 3166-1 alpha-3 country codes (USA, GBR, DEU, FRA, CAN, etc.).
"""
        prompts.append((event_id, sub_event_id, prompt))

    return prompts


def _sanitize_material(material: Dict[str, Any]) -> None:
    """Sanitize a single supplemental material (modifies in place)."""
    # Required string fields with defaults
    defaults = {
        "MaterialID": "",
        "EventID": "",
        "Sub-eventID": "",
        "reference_type": "bibliography",
        "verbatim_reference": "",
        "availability": "unknown",
    }
    for key, default in defaults.items():
        if material.get(key) is None:
            material[key] = default

    # Validate and normalize reference_type
    ref_type = material.get("reference_type", "")
    if ref_type == "map":
        # Maps are typically referenced in endnotes or footnotes
        # Default to endnote for map references
        material["reference_type"] = "endnote"
    elif ref_type not in ["endnote", "footnote", "bibliography"]:
        logger.warning(
            "Invalid reference_type '%s', defaulting to 'bibliography'", ref_type
        )
        material["reference_type"] = "bibliography"

    # Required array fields
    if material.get("resource_urls") is None:
        material["resource_urls"] = []

    # Citation defaults
    citation = material.get("citation", {})
    if citation.get("title") is None:
        citation["title"] = "Unknown"
    if citation.get("author") is None:
        citation["author"] = []


def sanitize_supplemental_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize supplemental data to ensure schema compliance."""
    # Event-level defaults
    defaults = {
        "Sub-event_Name": "",
        "Event_Name": "",
        "EventID": "",
        "Sub-eventID": "",
    }
    for key, default in defaults.items():
        if data.get(key) is None:
            data[key] = default

    # Sanitize each material
    for material in data.get("Supplemental_Material", []):
        _sanitize_material(material)

    return data


def validate_supplemental_json(data: Dict[str, Any]) -> None:
    """Validate supplemental material JSON against schema."""
    validate(instance=data, schema=SUPPLEMENTAL_SCHEMA)


def generate_ulids(data: Any) -> Any:
    """Replace GENERATE_NEW_ULID placeholders with actual ULIDs."""
    if isinstance(data, dict):
        return {k: generate_ulids(v) for k, v in data.items()}
    if isinstance(data, list):
        return [generate_ulids(item) for item in data]
    if isinstance(data, str) and data == "GENERATE_NEW_ULID":
        return str(ulid.new())
    return data


def extract_narrative_from_references(
    event_data: Dict[str, Any], grok_client: GrokClient
) -> list[Dict[str, Any]]:
    """Extract narrative content from footnotes/endnotes to create new sub-events."""
    prompt = f"""Analyze the footnotes and endnotes in this event for narrative content.

Event: {event_data.get('Event', {}).get('Event_Name', '')}

Extract ONLY footnotes/endnotes that contain historical narrative, context, or supplemental 
information beyond just citations. Ignore pure citations.

For each footnote/endnote with narrative content, return:
{{
  "Sub-eventID": "GENERATE_NEW_ULID",
  "Sub-event_summary": "Brief summary of the narrative content",
  "Sub-event_fulltext": {{
    "paragraph_1": "The narrative text from the footnote/endnote"
  }},
  "reference_source": "Footnote 4" or "Endnote 12",
  "Endnote_References": [],
  "Footnote_References": []
}}

Return JSON array of sub-events, or empty array [] if no narrative content found.
"""

    try:
        response = grok_client.extract_json(
            prompt=prompt,
            system_prompt="Extract narrative content from references.",
            temperature=0.1,
            use_cache=True,
            cache_type="supplemental_narrative",
        )

        if isinstance(response, list):
            return response
        return []
    except Exception as e:
        logger.debug("Error extracting narrative from references: %s", e)
        return []


def _append_to_file(
    file_path: Path, new_subevents: list[Dict[str, Any]], file_type: str
) -> None:
    """Append new sub-events to a JSON file, avoiding duplicates."""
    if not file_path.exists():
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Get existing reference sources
        existing_sources = {
            se.get("reference_source")
            for se in data["Event"]["Sub-events"]
            if se.get("reference_source")
        }

        # Filter out duplicates
        to_add = [
            se
            for se in new_subevents
            if se.get("reference_source") not in existing_sources
        ]

        if not to_add:
            return

        # Append and write
        data["Event"]["Sub-events"].extend(to_add)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info("Appended %d sub-event(s) to %s", len(to_add), file_path.name)

    except Exception as e:
        logger.error("Error updating %s file: %s", file_type, e)


def append_subevents_to_files(
    event_file: Path, new_subevents: list[Dict[str, Any]]
) -> None:
    """Append new sub-events to event.json and parsed.json files."""
    if not new_subevents:
        return

    # Update event file
    _append_to_file(event_file, new_subevents, "event")

    # Update parsed file
    parsed_file = event_file.parent / event_file.name.replace(
        "-event.json", "-parsed.json"
    )
    _append_to_file(parsed_file, new_subevents, "parsed")


def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]:
    """Load and validate event data from file."""
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            event_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Failed to load event file %s: %s", event_file, e)
        return None

    if "Event" not in event_data:
        logger.error(
            "Invalid event file structure in %s: missing 'Event' key", event_file
        )
        return None

    return event_data


def _extract_with_retry(
    prompt: str, sub_event_id: str, grok_client: GrokClient, max_retries: int = 3
) -> Optional[Dict[str, Any]]:
    """Extract supplemental material with retry logic."""
    for attempt in range(max_retries):
        try:
            response = grok_client.extract_json(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=0.1,
                use_cache=(attempt == 0),
                cache_type="supplemental",
            )

            response = generate_ulids(response)
            response = sanitize_supplemental_data(response)

            try:
                validate_supplemental_json(response)
            except ValidationError as e:
                logger.error(
                    "Validation error for sub-event %s: %s", sub_event_id, e.message
                )
                logger.debug("Invalid data: %s", json.dumps(response, indent=2))
                break  # Don't retry validation errors

            return response

        except Exception as e:  # pylint: disable=broad-exception-caught
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⚠ Attempt %d failed for sub-event %s: %s",
                    attempt + 1,
                    sub_event_id,
                    e,
                )
                logger.info("  Retrying (%d/%d)...", attempt + 2, max_retries)
            else:
                logger.error(
                    "  ✗ All %d attempts failed for sub-event %s: %s",
                    max_retries,
                    sub_event_id,
                    e,
                )

    return None


def _enrich_material(
    material: Dict[str, Any],
    grok_client: Any,
    openserp_url: str = "http://localhost:7001",
) -> None:
    """Enrich material with searches and copyright calculation (modifies in place)."""
    from src.extraction.copyright_calculator import determine_license
    from src.extraction.supplemental_search import sequential_search

    category = material.get("material_category", "referenced_material")

    # Only enrich referenced material
    if category != "referenced_material":
        return

    citation = material.get("citation", {})
    title = citation.get("title", "")
    author = citation.get("author", [])
    author_str = author[0] if author else None
    periodical = citation.get("periodical_name")

    # Search for URLs if not already present
    if not material.get("resource_urls"):
        url, source = sequential_search(
            title=title,
            author=author_str,
            periodical=periodical,
            grok_client=grok_client,
            openserp_url=openserp_url,
            search_gutenberg=True,
        )

        if url:
            material["resource_urls"] = [url]
            material["search_source"] = source
            logger.info("Found URL via %s: %s", source, url)

    # Calculate copyright
    license_status, license_notes = determine_license(material)
    if not material.get("license") or material.get("license") == "unknown":
        material["license"] = license_status
        material["license_notes"] = license_notes


def _resolve_entities_in_materials(
    materials: List[Dict[str, Any]],
    people_index: Dict[str, str],
    groups_index: Dict[str, str],
) -> None:
    """Resolve entity references in supplemental materials (modifies in place)."""
    for material in materials:
        citation = material.get("citation", {})
        authors = citation.get("author", [])
        verbatim = material.get("verbatim_reference", "")

        if authors:
            citation["author_ids"] = _resolve_author_ids(authors, people_index)

        mentioned_people = _resolve_mentioned_people(verbatim, people_index)
        mentioned_orgs = _resolve_mentioned_organizations(verbatim, groups_index)

        if mentioned_people:
            material["mentioned_people"] = mentioned_people
        if mentioned_orgs:
            material["mentioned_organizations"] = mentioned_orgs


def _separate_by_type(
    all_supplemental: List[Dict[str, Any]],
    people_index: Dict[str, str],
    groups_index: Dict[str, str],
    grok_client: Any = None,
    enrich: bool = True,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separate supplemental materials into endnotes and footnotes, resolving entities."""
    endnotes = []
    footnotes = []

    for sub_event_data in all_supplemental:
        materials = sub_event_data.get("Supplemental_Material", [])

        # Resolve entities
        _resolve_entities_in_materials(materials, people_index, groups_index)

        # Enrich with searches and copyright
        if enrich and grok_client:
            for material in materials:
                _enrich_material(material, grok_client)

        endnote_materials = [
            m for m in materials if m.get("reference_type") == "endnote"
        ]
        footnote_materials = [
            m for m in materials if m.get("reference_type") == "footnote"
        ]

        event_metadata = {
            "Event_Name": sub_event_data.get("Event_Name"),
            "EventID": sub_event_data.get("EventID"),
            "Sub-event_Name": sub_event_data.get("Sub-event_Name"),
            "Sub-eventID": sub_event_data.get("Sub-eventID"),
        }

        if endnote_materials:
            endnotes.append(
                {**event_metadata, "Supplemental_Material": endnote_materials}
            )

        if footnote_materials:
            footnotes.append(
                {**event_metadata, "Supplemental_Material": footnote_materials}
            )

    return endnotes, footnotes


def _write_supplemental_files(
    output_dir: Path,
    base_name: str,
    endnotes: List[Dict[str, Any]],
    footnotes: List[Dict[str, Any]],
) -> Optional[Path]:
    """Write endnotes and footnotes to separate files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files_written = []

    try:
        if endnotes:
            endnotes_file = output_dir / f"{base_name}-endnotes.json"
            with open(endnotes_file, "w", encoding="utf-8") as f:
                json.dump(endnotes, f, indent=2, ensure_ascii=False)
            total = sum(len(e.get("Supplemental_Material") or []) for e in endnotes)
            logger.info("Extracted %d endnote(s) to %s", total, endnotes_file.name)
            files_written.append(endnotes_file)

        if footnotes:
            footnotes_file = output_dir / f"{base_name}-footnotes.json"
            with open(footnotes_file, "w", encoding="utf-8") as f:
                json.dump(footnotes, f, indent=2, ensure_ascii=False)
            total = sum(len(f.get("Supplemental_Material") or []) for f in footnotes)
            logger.info("Extracted %d footnote(s) to %s", total, footnotes_file.name)
            files_written.append(footnotes_file)

        return files_written[0] if files_written else None

    except (OSError, IOError) as e:
        logger.error("Failed to write output files: %s", e)
        return None


def extract_supplemental(
    event_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
    output_root: Optional[Path] = None,
) -> Optional[Path]:
    """Extract supplemental material references from event file - Phase 1."""
    logger.info("Extracting supplemental material from %s", event_file.name)

    # Build entity indexes
    if output_root is None:
        output_root = (
            Path(output_dir).parent
            if not isinstance(output_dir, Path)
            else output_dir.parent
        )

    people_index = _build_people_index(output_root)
    groups_index = _build_groups_index(output_root)
    logger.debug(
        "Loaded %d people, %d groups for entity resolution",
        len(people_index),
        len(groups_index),
    )

    # Load event data
    event_data = _load_event_data(event_file)
    if not event_data:
        return None

    # Create prompts
    prompts = create_supplemental_prompt(event_data)
    if not prompts:
        logger.info("No sub-events found in %s", event_file.name)
        return None

    # Extract from API
    all_supplemental = []
    for _, sub_event_id, prompt in prompts:
        logger.debug("Processing sub-event %s", sub_event_id)
        response = _extract_with_retry(prompt, sub_event_id, grok_client)
        if response:
            all_supplemental.append(response)

    if not all_supplemental:
        logger.info("No supplemental material extracted from %s", event_file.name)
        return None

    # Process narrative subevents
    narrative_subevents = extract_narrative_from_references(event_data, grok_client)
    if narrative_subevents:
        narrative_subevents = generate_ulids(narrative_subevents)
        append_subevents_to_files(event_file, narrative_subevents)

    # Separate by type, resolve entities, and enrich
    endnotes, footnotes = _separate_by_type(
        all_supplemental, people_index, groups_index, grok_client, enrich=True
    )

    # Write output files
    base_name = event_file.name.replace("-event.json", "")
    return _write_supplemental_files(output_dir, base_name, endnotes, footnotes)
