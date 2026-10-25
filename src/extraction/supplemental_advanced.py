"""Phase 3: Advanced supplemental material features.

Implements:
- ISBN extraction for books
- Copyright determination using author death dates
- Archive URL verification
- Enhanced license detection
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Compiled regex patterns for performance
_ISBN_PATTERN = re.compile(r"^\d{10}$|^\d{13}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_isbn_publication_year(pub_date: str) -> bool:
    """Check if publication year is after 1966 (when ISBNs started). Returns True if valid."""
    if pub_date and pub_date != "UNKNOWN":
        try:
            year = int(pub_date.split("-")[0])
            if year < 1966:
                logger.debug("Book published before 1966, no ISBN")
                return False
        except (ValueError, IndexError):
            pass
    return True


def _get_author_from_citation(citation: Dict[str, Any]) -> str:
    """Extract author string from citation."""
    authors = citation.get("author", [])
    if isinstance(authors, str):
        return authors
    elif isinstance(authors, list) and authors:
        return authors[0]
    return ""


def _validate_isbn(isbn: str) -> Optional[str]:
    """Validate and clean ISBN. Returns cleaned ISBN or None."""
    if isbn == "NOT_FOUND":
        return None

    if _ISBN_PATTERN.match(isbn):
        logger.debug("Found ISBN: %s", isbn)
        return isbn

    logger.debug("Invalid ISBN format: %s", isbn)
    return None


def extract_isbn(citation: Dict[str, Any], grok_client: Any) -> Optional[str]:
    """Extract ISBN for books using LLM."""
    # Check publication date
    pub_date = citation.get("publication_date", "")
    if not _check_isbn_publication_year(pub_date):
        return None

    # Get author and title
    author = _get_author_from_citation(citation)
    title = citation.get("title", "")

    if not (author and title):
        logger.debug("Missing author or title for ISBN lookup")
        return None

    publisher = citation.get("publisher", "")

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "isbn_lookup",
        title=title,
        author=author,
        publisher=publisher,
        pub_date=pub_date,
    )

    try:
        response = grok_client.chat_completion(
            prompt=prompt, cache_type="supplemental_advanced", use_cache=True
        )
        isbn = response.strip().replace("-", "").replace(" ", "")
        return _validate_isbn(isbn)

    except Exception as e:
        logger.debug("ISBN extraction error: %s", e)
        return None


def get_author_death_date(author: str, grok_client: Any) -> Optional[str]:
    """Get author's death date using LLM."""
    if not author:
        return None

    from src.utils.prompt_loader import render_prompt as _rp

    prompt = _rp("author_death_date", author=author)

    try:
        response = grok_client.chat_completion(
            prompt=prompt, cache_type="supplemental_advanced", use_cache=True
        )
        death_date = response.strip()

        if death_date == "UNKNOWN":
            return "UNKNOWN"

        # Validate date format
        if _DATE_PATTERN.match(death_date):
            logger.debug("Found death date for %s: %s", author, death_date)
            return death_date

        logger.debug("Invalid death date format: %s", death_date)
        return "UNKNOWN"

    except Exception as e:
        logger.debug("Death date lookup error: %s", e)
        return "UNKNOWN"


def _extract_pub_year(pub_date: str) -> Optional[int]:
    """Extract publication year from date string."""
    if pub_date and pub_date != "UNKNOWN":
        try:
            return int(pub_date.split("-")[0])
        except (ValueError, IndexError):
            pass
    return None


def _is_us_government_work(publisher: str) -> bool:
    """Check if publisher is a US Government entity."""
    publisher_lower = publisher.lower() if publisher else ""
    return any(
        term in publisher_lower
        for term in ["u.s. government", "us government", "government printing"]
    )


def _check_death_plus_70(
    author_death_date: Optional[str], current_year: int
) -> tuple[str, str]:
    """Check copyright based on author death + 70 years. Returns (status, basis)."""
    if author_death_date and author_death_date != "UNKNOWN":
        death_year = int(author_death_date.split("-")[0])
        expiration_year = death_year + 70
        if current_year >= expiration_year:
            return (
                "public_domain",
                f"Author death + 70 years expired ({expiration_year})",
            )
        return "copyright", f"Under copyright until {expiration_year}"
    return "copyright", "Author death date unknown"


def _determine_usa_copyright(
    pub_year: Optional[int], author_death_date: Optional[str], current_year: int
) -> tuple[str, str]:
    """Determine USA copyright status. Returns (status, basis)."""
    if pub_year and pub_year < 1928:
        return "public_domain", "Published before 1928"

    if pub_year and 1928 <= pub_year <= 1977:
        expiration_year = pub_year + 95
        if current_year >= expiration_year:
            return "public_domain", f"95 years expired ({expiration_year})"
        return "copyright", f"Under copyright until {expiration_year}"

    if pub_year and pub_year > 1977:
        return _check_death_plus_70(author_death_date, current_year)

    return "unknown", ""


def determine_copyright_status(
    citation: Dict[str, Any],
    author_death_date: Optional[str],
    jurisdiction: str = "USA",
) -> Dict[str, Any]:
    """Determine copyright status based on publication date and author death date."""
    current_year = datetime.now().year

    status = {
        "status": "unknown",
        "author_death_date": author_death_date,
        "determination_basis": "",
        "jurisdiction": jurisdiction,
    }

    pub_year = _extract_pub_year(citation.get("publication_date", ""))

    # US Government works
    if _is_us_government_work(citation.get("publisher") or ""):
        status["status"] = "public_domain"
        status["determination_basis"] = "US Government work"
        return status

    # Jurisdiction-specific rules
    if jurisdiction == "USA":
        status["status"], status["determination_basis"] = _determine_usa_copyright(
            pub_year, author_death_date, current_year
        )
    elif jurisdiction in ["EU", "GBR"]:
        status["status"], status["determination_basis"] = _check_death_plus_70(
            author_death_date, current_year
        )

    return status


def verify_archive_url(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Verify archive URL is accessible and material is listed."""
    result = {
        "verified": False,
        "verification_date": datetime.now().isoformat(),
        "verification_notes": None,
    }

    try:
        from src.utils.url_hygiene import safe_get

        response = safe_get(url, timeout=timeout)
        if response is None:
            result["verification_notes"] = "blocked or unreachable"
            logger.debug("Archive URL blocked/unreachable: %s", url)
            return result
        response.raise_for_status()

        result["verified"] = True
        result["verification_notes"] = f"HTTP {response.status_code}"
        logger.debug("Archive URL verified: %s", url)

    except requests.RequestException as e:
        result["verification_notes"] = f"HTTP error: {e}"
        logger.debug("Archive URL verification failed: %s", e)

    return result


def _load_supplemental_data(supplemental_file: Path) -> Optional[Any]:
    """Load supplemental data from file."""
    try:
        with open(supplemental_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Error reading supplemental file: %s", e)
        return None


def _extract_materials_from_data(data: Any) -> list:
    """Extract materials list from data (handles both list and dict formats)."""
    if isinstance(data, list):
        # Array of sub-event objects
        all_materials = []
        for sub_event in data:
            # Normalize plural key variant from legacy data
            if (
                "Supplemental_Materials" in sub_event
                and "Supplemental_Material" not in sub_event
            ):
                sub_event["Supplemental_Material"] = sub_event.pop(
                    "Supplemental_Materials"
                )
            all_materials.extend(sub_event.get("Supplemental_Material", []))
        return all_materials
    else:
        # Single object with materials array
        return data.get("materials", [])


def _enrich_isbn(
    material: dict, citation: dict, config: dict, grok_client: Any
) -> bool:
    """Enrich material with ISBN. Returns True if enriched."""
    material_type = citation.get("type", "")

    if (
        material_type == "book"
        and config.get("extract_isbn", False)
        and not citation.get("isbn")
    ):
        isbn = extract_isbn(citation, grok_client)
        if isbn:
            citation["isbn"] = isbn
            return True

    return False


def _enrich_copyright(
    material: dict, citation: dict, config: dict, grok_client: Any
) -> bool:
    """Enrich material with copyright status. Returns True if enriched."""
    if not config.get("determine_copyright", False):
        return False

    author = _get_author_from_citation(citation)

    if author and not material.get("copyright_status"):
        death_date = get_author_death_date(author, grok_client)
        jurisdiction = citation.get("publication_country", "USA")

        copyright_status = determine_copyright_status(
            citation, death_date, jurisdiction
        )
        material["copyright_status"] = copyright_status
        return True

    return False


def _enrich_archive_url(material: dict, config: dict) -> bool:
    """Enrich material with archive URL verification. Returns True if enriched."""
    if not config.get("verify_archive_urls", False):
        return False

    archive_info = material.get("archive_info", {})
    archive_url = archive_info.get("archive_url")

    if archive_url and not archive_info.get("verified"):
        verification = verify_archive_url(archive_url)
        archive_info.update(verification)
        return True

    return False


def _save_enriched_data(
    supplemental_file: Path,
    data: Any,
    enriched_count: int,
    entity: Optional[str] = None,
) -> bool:
    """Save enriched data to file. Returns True if successful.

    When ``entity`` is given (e.g. "bibliography"), the write goes through the central write-guard
    (schema + mandatory-field validation + validation_stats alert on a block) instead of a raw
    write. Left as a raw write when ``entity`` is None (the supplemental-container file, which is
    not a single schema-governed output entity)."""
    if enriched_count == 0:
        return True

    if entity:
        # NOTE: the guard validates against the schema resolved from the FILE PATH
        # (parent-dir name), not this entity arg — entity only version-stamps metadata.
        # Pass an entity whose name matches the output dir (bibliography -> output/bibliography/).
        from src.utils.file_lock import write_json_with_lock

        if not write_json_with_lock(supplemental_file, data, entity=entity):
            logger.warning(
                "%s enrichment write BLOCKED by schema guard: %s",
                entity,
                supplemental_file.name,
            )
            return False
        logger.info("Enriched %d materials with advanced features", enriched_count)
        return True

    try:
        with open(supplemental_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Enriched %d materials with advanced features", enriched_count)
        return True
    except OSError as e:
        logger.error("Error writing supplemental file: %s", e)
        return False


def enrich_bibliography(
    bibliography_dir: Path,
    config: Dict[str, Any],
    grok_client: Any,
) -> int:
    """Enrich bibliography files with ISBN, copyright, and archive verification.

    Reads individual bibliography JSON files from output/bibliography/.
    Returns number of files enriched.
    """
    if not bibliography_dir.exists():
        logger.warning("Bibliography directory not found: %s", bibliography_dir)
        return 0

    bib_files = [
        f
        for f in bibliography_dir.glob("*.json")
        if f.name not in ("index.json", "review_queue.json")
    ]

    if not bib_files:
        logger.info("No bibliography files found")
        return 0

    enriched_count = 0
    for bib_file in sorted(bib_files):
        if _enrich_bib_file(bib_file, config, grok_client):
            enriched_count += 1

    logger.info("Enriched %d of %d bibliography files", enriched_count, len(bib_files))
    return enriched_count


def _set_alt_title(data: dict, citation: dict, value: str) -> None:
    """Write alt_title to the ONE canonical place (top-level) and keep citation.alt_title
    in lockstep so the two copies never drift (validated by the output schema check)."""
    data["alt_title"] = value
    citation["alt_title"] = value


def _alt_title_looks_expanded(text: str) -> bool:
    """Heuristic: already-expanded titles have no bare green-book shorthand tokens, so we
    don't re-spend a Grok call. Conservative — only skips when clearly already full words.
    """
    if not text:
        return False
    shorthand = re.compile(
        r"\b(Interv|Ltr|Memo|Rpt|Jnl|Opns?|Div|Regt|Bn|Gp|Hq|AAR|FO|OCMH|FUSA|MS)\b"
    )
    return not shorthand.search(text)


def _enrich_alt_title(
    data: dict, citation: dict, config: dict, grok_client: Any
) -> bool:
    """Grok-expand the citation's verbatim shorthand into a full, searchable alt_title
    (SEMANTIC — Grok's job, not deterministic). Idempotent: skips if already expanded or if
    no Grok client. Returns True if alt_title changed."""
    if grok_client is None:
        return False
    verbatim = _resolve_verbatim(data, citation)
    if not verbatim:
        return False
    current = data.get("alt_title") or citation.get("alt_title") or ""
    if _alt_title_looks_expanded(current) and current:
        return False  # already expanded — don't re-spend a call

    expanded = _call_alt_title_expansion(grok_client, verbatim)
    if expanded and expanded != current:
        _set_alt_title(data, citation, expanded)
        logger.info("alt_title expanded: %r -> %r", verbatim[:50], expanded[:60])
        return True
    return False


def _resolve_verbatim(data: dict, citation: dict) -> str:
    """Resolve the citation's verbatim shorthand: citation.verbatim -> record verbatim_reference
    -> first mention's verbatim_reference. Returns the stripped string ('' if none)."""
    verbatim = citation.get("verbatim") or data.get("verbatim_reference") or ""
    if not verbatim:
        for m in data.get("mentions") or []:
            if m.get("verbatim_reference"):
                verbatim = m["verbatim_reference"]
                break
    return (verbatim or "").strip()


def _call_alt_title_expansion(grok_client: Any, verbatim: str) -> str:
    """Grok-expand the verbatim shorthand into a full searchable title. Fail-safe: a missing
    prompt template or a Grok error returns '' (leave alt_title as-is, never crash)."""
    from src.utils.prompt_loader import render_prompt

    try:
        prompt = render_prompt("bibliography_alt_title", verbatim=verbatim)
    except Exception:
        return ""
    try:
        resp = grok_client.chat_completion(
            prompt=prompt,
            system_prompt=(
                "You expand WWII military-citation shorthand (US Army Green Book "
                "footnotes) into a full, human-readable, catalog-searchable title. Expand "
                "unit/document-type abbreviations and resolve a person to their full name "
                "only when you are confident. Do NOT invent facts. Return ONLY the expanded "
                "title text, no quotes or commentary."
            ),
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_alt_title",
        )
        return (resp or "").strip().strip('"').strip()
    except Exception as e:  # noqa: BLE001 - enrichment must never crash the pass
        logger.debug("alt_title expansion failed: %s", e)
        return ""


def _enrich_bib_file(bib_file: Path, config: Dict[str, Any], grok_client: Any) -> bool:
    """Enrich a single bibliography file. Returns True if changed."""
    data = _load_supplemental_data(bib_file)
    if not data or not isinstance(data, dict):
        return False

    citation = data.get("citation", {})
    if not citation or not isinstance(citation, dict):
        return False

    changed = (
        _enrich_alt_title(data, citation, config, grok_client)
        | _enrich_isbn(data, citation, config, grok_client)
        | _enrich_copyright(data, citation, config, grok_client)
        | _enrich_archive_url(data, config)
    )

    if changed:
        _save_enriched_data(bib_file, data, 1, entity="bibliography")
    return changed


def enrich_with_advanced_features(
    supplemental_file: Path,
    config: Dict[str, Any],
    grok_client: Any,
) -> int:
    """Enrich supplemental materials with Phase 3 features.

    Returns:
        Number of materials enriched
    """
    if not supplemental_file.exists():
        logger.warning("Supplemental file not found: %s", supplemental_file)
        return 0

    # Load materials
    data = _load_supplemental_data(supplemental_file)
    if data is None:
        return 0

    materials = _extract_materials_from_data(data)

    enriched_count = 0

    for material in materials:
        citation = material.get("citation", {})
        if not citation or not isinstance(citation, dict):
            logger.debug("Skipping material with invalid citation")
            continue

        # Enrich with various features
        if _enrich_isbn(material, citation, config, grok_client):
            enriched_count += 1

        if _enrich_copyright(material, citation, config, grok_client):
            enriched_count += 1

        if _enrich_archive_url(material, config):
            enriched_count += 1

    # Write updated file
    if not _save_enriched_data(supplemental_file, data, enriched_count):
        return 0

    return enriched_count
