"""Supplemental material extraction from event data - Phase 1: Core Extraction."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import ulid
from jsonschema import ValidationError, validate

from src.grok_client import GrokClient
from src.utils.json_validator import _fix_invalid_ulids
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


def _build_ref_context(
    endnote_refs: List,
    footnote_refs: List,
    endnote_texts: Optional[Dict[int, str]],
) -> tuple:
    """Build endnote text block and type hint for a sub-event."""
    from src.extraction.fetch_endnotes import format_endnote_text_block

    all_refs = [r for r in endnote_refs + footnote_refs if isinstance(r, int)]
    endnote_block = ""
    if endnote_texts and all_refs:
        endnote_block = format_endnote_text_block(endnote_texts, all_refs)
        return endnote_block, ""

    ref_type_hint = ""
    if endnote_refs and not footnote_refs:
        ref_type_hint = "\nNote: These are endnotes (on a separate page from the text)."
    elif footnote_refs and not endnote_refs:
        ref_type_hint = (
            "\nNote: These are footnotes (at the bottom of the same page as the text)."
        )
    return endnote_block, ref_type_hint


def create_supplemental_prompt(
    event_data: Dict[str, Any],
    endnote_texts: Optional[Dict[int, str]] = None,
) -> List[tuple]:
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
        endnote_block, ref_type_hint = _build_ref_context(
            endnote_refs, footnote_refs, endnote_texts
        )

        prompt = f"""Classify and extract endnote/footnote references from this sub-event.

Event: {event_title}
EventID: {event_id}
Sub-event: {sub_event_summary}
Sub-eventID: {sub_event_id}

Text:
{text}

Endnote References Found: {endnote_refs}
Footnote References Found: {footnote_refs}
{endnote_block}
{ref_type_hint}

CLASSIFICATION: Each endnote/footnote must be classified as one of:
- "document_reference" — a citation to a book, report, memo, field order, AAR, letter,
  journal article, or any other document. The note tells you WHERE information came from.
- "factual_content" — historical narrative containing facts: casualties, awards, troop
  movements, dates, equipment details, weather, or other extractable information.
  The note tells you WHAT happened.
- "ambiguous" — cannot clearly determine if it is a document reference or factual content.

MIXED ENTRIES: If a single endnote/footnote contains BOTH a factual statement AND a
document citation, split it into TWO separate entries sharing the same reference_number:
one classified as "factual_content" and one as "document_reference".

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
      "content_class": "document_reference|factual_content|ambiguous",
      "reference_type": "endnote|footnote|bibliography",
      "reference_number": "number or symbol from text",
      "verbatim_reference": "EXACT text from the source document — do NOT invent references",
      "citation": {{
        "author": ["Last, First"],
        "title": "Title from the source text",
        "alt_title": "Expanded/unabbreviated form of the title, or null if title is already full",
        "publisher": "Publisher or null",
        "publication_date": "YYYY or YYYY-MM-DD or null",
        "first_edition_date": "YYYY or null",
        "publication_location": "City or null",
        "publication_country": "ISO 3166-1 alpha-3 or null",
        "isbn": "ISBN or null",
        "isbn_edition": "ISBN of specific edition or null",
        "pages": "page range or null",
        "volume": "volume or null",
        "edition": "edition or null",
        "translator": "translator or null",
        "periodical_name": "journal/newspaper name or null",
        "document_type": "Primary source document or null",
        "author_death_date": "YYYY or YYYY-MM-DD or null"
      }},
      "availability": "online|offline|archive|unknown",
      "resource_urls": [],
      "archive_reference_number": "archive ref or null",
      "archive_physical_address": "archive address or null",
      "license": "public_domain|copyright|unknown",
      "license_notes": "brief license note"
    }}
  ]
}}

CRITICAL: Only extract references that actually appear in the source text above.
Do NOT invent, fabricate, or copy example references. If "Actual endnote/footnote text"
is provided above, use that as the verbatim_reference — it is the real text from the
source document. If no actual text is provided, use the reference number only and set
verbatim_reference to the reference number (e.g. "5"). Do NOT fabricate citation text.
If no references are found, return an empty Supplemental_Material array.

MULTIPLE SOURCES IN ONE REFERENCE: A single endnote/footnote often contains multiple
distinct sources separated by semicolons, periods, or other delimiters. Each distinct
source MUST become its own Supplemental_Material entry with a separate citation object.
They share the same reference_number but each gets its own MaterialID.
Example: "Eisenhower, Crusade in Europe (1948), pp 245; Answers by Smith, OCMH Files."
= TWO entries, both "document_reference".

CLASSIFICATION NOTES for citation fields:
- For "document_reference": populate citation fields fully.
  Use alt_title to expand military abbreviations (e.g., "357th Inf Jnl" → "357th Infantry Journal").
- For "factual_content": set citation to null. The verbatim_reference IS the content.
- For "ambiguous": populate citation if possible, preserve verbatim_reference.

RESOURCE LOCATION RULES:
- resource_urls must point to where the CITED DOCUMENT can be found, NOT the HyperWar
  page where the footnote/endnote appears. HyperWar URLs are the source we're extracting
  FROM, not the resource being cited. Only include a URL if the cited document itself is
  available online (e.g., a digitized report, a journal article URL).
- For military files, memos, cables, and government records (e.g., "SHAEF SGS file",
  "FUSA AAR", "AG files", "OCMH files"), set availability to "archive" and use your
  knowledge to populate archive_physical_address (typically "National Archives and Records
  Administration (NARA), College Park, MD, USA" for US military records).
- For published books, set availability to "offline" unless you know of a specific online
  edition. Do NOT guess URLs.
- Only set availability to "online" when the cited material itself has a known URL.
For author_death_date, include if known (format: YYYY or YYYY-MM-DD).
For government/educational institutions, use license "public_domain".
For commercial publishers, use license "copyright".
If uncertain, use license "unknown".
Use ISO 3166-1 alpha-3 country codes (USA, GBR, DEU, FRA, CAN, etc.).
"""
        try:
            from src.utils.prompt_loader import render_prompt

            prompt = render_prompt(
                "supplemental",
                event_title=event_title,
                event_id=event_id,
                sub_event_summary=sub_event_summary,
                sub_event_id=sub_event_id,
                text=text,
                endnote_refs=endnote_refs,
                footnote_refs=footnote_refs,
                endnote_block=endnote_block,
                ref_type_hint=ref_type_hint,
            )
        except Exception:
            pass
        prompts.append((event_id, sub_event_id, prompt))

    return prompts


def _sanitize_material(material: Dict[str, Any]) -> None:
    """Sanitize a single supplemental material (modifies in place)."""
    # Required string fields with defaults
    defaults = {
        "MaterialID": "",
        "EventID": "",
        "Sub-eventID": "",
        "content_class": "document_reference",
        "reference_type": "bibliography",
        "verbatim_reference": "",
        "availability": "unknown",
    }
    for key, default in defaults.items():
        if material.get(key) is None:
            material[key] = default

    # Validate content_class
    valid_classes = {"document_reference", "factual_content", "ambiguous"}
    if material.get("content_class") not in valid_classes:
        material["content_class"] = "document_reference"

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
    citation = material.get("citation") or {}
    material["citation"] = citation
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
            response = _fix_invalid_ulids(response)
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


def _enrich_materials(materials: list, grok_client: Any, enrich: bool) -> None:
    """Enrich materials with searches and copyright if enabled."""
    if enrich and grok_client:
        for material in materials:
            _enrich_material(material, grok_client)


def _filter_by_reference_type(materials: list, ref_type: str) -> list:
    """Filter materials by reference type."""
    return [m for m in materials if m.get("reference_type") == ref_type]


def _create_event_metadata(sub_event_data: dict) -> dict:
    """Create event metadata dictionary."""
    return {
        "Event_Name": sub_event_data.get("Event_Name"),
        "EventID": sub_event_data.get("EventID"),
        "Sub-event_Name": sub_event_data.get("Sub-event_Name"),
        "Sub-eventID": sub_event_data.get("Sub-eventID"),
    }


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
        _enrich_materials(materials, grok_client, enrich)

        # Filter by type
        endnote_materials = _filter_by_reference_type(materials, "endnote")
        footnote_materials = _filter_by_reference_type(materials, "footnote")

        # Create event metadata
        event_metadata = _create_event_metadata(sub_event_data)

        if endnote_materials:
            endnotes.append(
                {**event_metadata, "Supplemental_Material": endnote_materials}
            )

        if footnote_materials:
            footnotes.append(
                {**event_metadata, "Supplemental_Material": footnote_materials}
            )

    return endnotes, footnotes


def _get_source_copyright_year(event_file: Path) -> Optional[int]:
    """Get copyright year from the source book's metadata."""
    book_dir = event_file.parent.name  # e.g. "BreakoutAndPursuit"
    content_root = Path("contentrepository") / book_dir
    if not content_root.exists():
        return None
    # Check first chapter's metadata for copyright_date
    for meta in sorted(content_root.rglob("*-meta.yaml")):
        try:
            import yaml

            with open(meta, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            year = data.get("copyright_date")
            if year:
                return int(str(year).strip("'\""))
        except (ValueError, OSError):
            continue
    return None


def _filter_anachronistic_citations(
    groups: List[Dict[str, Any]], source_year: int
) -> List[Dict[str, Any]]:
    """Remove citations with publication dates after the source book's copyright year."""
    for group in groups:
        materials = group.get("Supplemental_Material", [])
        filtered = []
        for mat in materials:
            citation = mat.get("citation") or {}
            pub_date = citation.get("publication_date") or ""
            try:
                pub_year = int(str(pub_date)[:4])
            except (ValueError, IndexError):
                filtered.append(mat)
                continue
            if pub_year > source_year:
                logger.warning(
                    "Removing anachronistic citation: '%s' (%s) post-dates source (%d)",
                    citation.get("title", "unknown"),
                    pub_date,
                    source_year,
                )
            else:
                filtered.append(mat)
        group["Supplemental_Material"] = filtered
    return groups


def _route_by_content_class(
    all_supplemental: List[Dict[str, Any]],
    bib_dir: Path,
    book: str,
    chapter: str,
    grok_client: Any = None,
    enrich: bool = False,
) -> tuple:
    """Route materials by content_class. Returns (factual_items, ambiguous_items, bib_count)."""
    factual_items = []
    ambiguous_items = []
    bib_count = 0

    for sub_event_data in all_supplemental:
        event_id = sub_event_data.get("EventID", "")
        sub_event_id = sub_event_data.get("Sub-eventID", "")
        sub_event_name = sub_event_data.get("Sub-event_Name", "")

        for material in sub_event_data.get("Supplemental_Material", []):
            content_class = material.get("content_class", "document_reference")

            if content_class == "document_reference":
                if enrich:
                    _enrich_material(material, grok_client)
                from src.extraction.bibliography import store_bibliography_entry

                store_bibliography_entry(bib_dir, material, book, chapter)
                bib_count += 1

            elif content_class == "factual_content":
                factual_items.append(
                    {
                        "verbatim_reference": material.get("verbatim_reference", ""),
                        "reference_type": material.get("reference_type", ""),
                        "reference_number": material.get("reference_number", ""),
                        "source_EventID": event_id,
                        "source_Sub-eventID": sub_event_id,
                        "source_Sub-event_Name": sub_event_name,
                        "BibliographyID": material.get("_BibliographyID"),
                    }
                )

            elif content_class == "ambiguous":
                ambiguous_items.append(
                    {
                        "book": book,
                        "chapter": chapter,
                        "reference_type": material.get("reference_type", ""),
                        "reference_number": material.get("reference_number", ""),
                        "verbatim_reference": material.get("verbatim_reference", ""),
                        "EventID": event_id,
                        "Sub-eventID": sub_event_id,
                    }
                )

    return factual_items, ambiguous_items, bib_count


def _write_notes_event(
    event_file: Path,
    factual_items: List[Dict[str, Any]],
) -> Optional[Path]:
    """Write factual content as a notes-event file alongside the source event."""
    if not factual_items:
        return None

    # Read metadata from source event file
    source_meta = {}
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            source_data = json.load(f)
        source_event = source_data.get("Event", source_data)
        source_meta = {
            "book": source_event.get("book", ""),
            "author": source_event.get("author", ""),
            "series": source_event.get("series", ""),
        }
    except Exception:
        pass

    notes_file = event_file.with_name(
        event_file.name.replace("-event.json", "-notes-event.json")
    )

    sub_events = []
    for item in factual_items:
        sub_events.append(
            {
                "Sub-eventID": str(ulid.new()),
                "Sub-event_summary": item["verbatim_reference"][:200],
                "Sub-event_fulltext": {"paragraph_1": item["verbatim_reference"]},
                "source_reference": {
                    "reference_type": item["reference_type"],
                    "reference_number": item["reference_number"],
                    "source_EventID": item["source_EventID"],
                    "source_Sub-eventID": item["source_Sub-eventID"],
                    "BibliographyID": item.get("BibliographyID"),
                },
                "Endnote_References": [],
                "Footnote_References": [],
            }
        )

    event_data = {
        "Event": {
            "EventID": str(ulid.new()),
            "Event_Name": f"Notes: {event_file.stem.replace('-event', '')}",
            "book": source_meta.get("book", ""),
            "author": source_meta.get("author", ""),
            "series": source_meta.get("series", ""),
            "Sub-events": sub_events,
        }
    }

    with open(notes_file, "w", encoding="utf-8") as f:
        json.dump(event_data, f, indent=2, ensure_ascii=False)

    logger.info("Wrote %d factual items to %s", len(sub_events), notes_file.name)
    return notes_file


def _append_to_review_queue(bib_dir: Path, items: List[Dict[str, Any]]) -> None:
    """Append ambiguous items to review_queue.json."""
    if not items:
        return
    queue_file = bib_dir / "review_queue.json"
    existing = []
    if queue_file.exists():
        with open(queue_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing.extend(items)
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    logger.info("Added %d ambiguous items to review queue", len(items))


def extract_supplemental(
    event_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    enrich: bool = False,
) -> Optional[Path]:
    """Extract and classify endnotes/footnotes from event file.

    Routes document references to output/bibliography/,
    factual content to {chapter}-notes-event.json,
    and ambiguous items to review_queue.json.

    Args:
        enrich: If True, search online/offline repositories for bibliography URLs
                and determine copyright status before storing.
    """
    logger.info("Extracting supplemental material from %s", event_file.name)

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

    # Fetch actual endnote/footnote text from ibiblio
    from src.extraction.fetch_endnotes import fetch_endnote_texts

    parsed_file = event_file.with_name(
        event_file.name.replace("-event.json", "-parsed.json")
    )
    endnote_texts = fetch_endnote_texts(parsed_file)

    # Create prompts
    prompts = create_supplemental_prompt(event_data, endnote_texts)
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

    # Sanitize and generate ULIDs
    for sub_event_data in all_supplemental:
        sanitize_supplemental_data(sub_event_data)
    all_supplemental = generate_ulids(all_supplemental)

    # Route by content class
    book = event_file.parent.name
    chapter = event_file.name.replace("-event.json", "")
    bib_dir = output_root / "bibliography"

    factual, ambiguous, bib_count = _route_by_content_class(
        all_supplemental, bib_dir, book, chapter, grok_client, enrich
    )

    # Write factual content as notes-event file
    notes_file = _write_notes_event(event_file, factual)

    # Queue ambiguous items for human review
    _append_to_review_queue(bib_dir, ambiguous)

    logger.info(
        "  %s: %d bibliography, %d factual, %d ambiguous",
        event_file.name,
        bib_count,
        len(factual),
        len(ambiguous),
    )

    return notes_file or (bib_dir if bib_count > 0 else None)
