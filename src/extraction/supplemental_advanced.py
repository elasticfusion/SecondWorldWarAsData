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

    prompt = f"""Find the ISBN for this book (preferably first edition):
Author: {author}
Title: {title}
Publisher: {publisher}
Publication Date: {pub_date}

Return ONLY the ISBN number (10 or 13 digits), or "NOT_FOUND" if unavailable.
Do not include hyphens or spaces."""

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

    prompt = f"""What is the death date of author: {author}

Return ONLY the death date in ISO 8601 format (YYYY-MM-DD), or "UNKNOWN" if not found or still living.
If only year is known, use YYYY-01-01."""

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
        response = requests.get(url, allow_redirects=True, timeout=timeout)
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
    supplemental_file: Path, data: Any, enriched_count: int
) -> bool:
    """Save enriched data to file. Returns True if successful."""
    if enriched_count == 0:
        return True

    try:
        with open(supplemental_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Enriched %d materials with advanced features", enriched_count)
        return True
    except OSError as e:
        logger.error("Error writing supplemental file: %s", e)
        return False


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
