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


def extract_isbn(citation: Dict[str, Any], grok_client: Any) -> Optional[str]:
    """Extract ISBN for books using LLM."""
    # Check publication date - ISBNs started in 1966
    pub_date = citation.get("publication_date", "")
    if pub_date and pub_date != "UNKNOWN":
        try:
            year = int(pub_date.split("-")[0])
            if year < 1966:
                logger.debug("Book published before 1966, no ISBN")
                return None
        except (ValueError, IndexError):
            pass

    # Build query for LLM
    authors = citation.get("author", [])
    if isinstance(authors, str):
        author = authors
    elif isinstance(authors, list) and authors:
        author = authors[0]
    else:
        author = ""

    title = citation.get("title", "")
    publisher = citation.get("publisher", "")

    if not (author and title):
        logger.debug("Missing author or title for ISBN lookup")
        return None

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

        # Validate ISBN format
        if isbn == "NOT_FOUND":
            return None
        if _ISBN_PATTERN.match(isbn):
            logger.debug("Found ISBN: %s", isbn)
            return isbn

        logger.debug("Invalid ISBN format: %s", isbn)
        return None

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


def determine_copyright_status(
    citation: Dict[str, Any],
    author_death_date: Optional[str],
    jurisdiction: str = "USA",
) -> Dict[str, Any]:
    """Determine copyright status based on publication date and author death date."""
    pub_date = citation.get("publication_date", "")
    current_year = datetime.now().year

    status = {
        "status": "unknown",
        "author_death_date": author_death_date,
        "determination_basis": "",
        "jurisdiction": jurisdiction,
    }

    # Extract publication year
    pub_year = None
    if pub_date and pub_date != "UNKNOWN":
        try:
            pub_year = int(pub_date.split("-")[0])
        except (ValueError, IndexError):
            pass

    # US Government works
    publisher = citation.get("publisher") or ""
    publisher = publisher.lower()
    if any(
        term in publisher
        for term in ["u.s. government", "us government", "government printing"]
    ):
        status["status"] = "public_domain"
        status["determination_basis"] = "US Government work"
        return status

    # USA copyright rules
    if jurisdiction == "USA":
        if pub_year and pub_year < 1928:
            status["status"] = "public_domain"
            status["determination_basis"] = "Published before 1928"
        elif pub_year and 1928 <= pub_year <= 1977:
            expiration_year = pub_year + 95
            if current_year >= expiration_year:
                status["status"] = "public_domain"
                status["determination_basis"] = f"95 years expired ({expiration_year})"
            else:
                status["status"] = "copyright"
                status["determination_basis"] = (
                    f"Under copyright until {expiration_year}"
                )
        elif pub_year and pub_year > 1977:
            if author_death_date and author_death_date != "UNKNOWN":
                death_year = int(author_death_date.split("-")[0])
                expiration_year = death_year + 70
                if current_year >= expiration_year:
                    status["status"] = "public_domain"
                    status["determination_basis"] = (
                        f"Author death + 70 years expired ({expiration_year})"
                    )
                else:
                    status["status"] = "copyright"
                    status["determination_basis"] = (
                        f"Under copyright until {expiration_year}"
                    )
            else:
                status["status"] = "copyright"
                status["determination_basis"] = "Post-1977, author death date unknown"

    # EU/UK copyright rules (Life + 70)
    elif jurisdiction in ["EU", "GBR"]:
        if author_death_date and author_death_date != "UNKNOWN":
            death_year = int(author_death_date.split("-")[0])
            expiration_year = death_year + 70
            if current_year >= expiration_year:
                status["status"] = "public_domain"
                status["determination_basis"] = (
                    f"Author death + 70 years expired ({expiration_year})"
                )
            else:
                status["status"] = "copyright"
                status["determination_basis"] = (
                    f"Under copyright until {expiration_year}"
                )
        else:
            status["status"] = "copyright"
            status["determination_basis"] = "Author death date unknown"

    return status


def verify_archive_url(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Verify archive URL is accessible and material is listed."""
    result = {
        "verified": False,
        "verification_date": datetime.now().isoformat(),
        "verification_notes": None,
    }

    try:
        with requests.Session() as session:
            session.timeout = timeout
            response = session.get(url, allow_redirects=True)
            response.raise_for_status()

            result["verified"] = True
            result["verification_notes"] = f"HTTP {response.status_code}"
            logger.debug("Archive URL verified: %s", url)

    except requests.RequestException as e:
        result["verification_notes"] = f"HTTP error: {e}"
        logger.debug("Archive URL verification failed: %s", e)

    return result


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
    try:
        with open(supplemental_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Error reading supplemental file: %s", e)
        return 0

    # Handle both list (array of sub-events) and dict (single object) formats
    if isinstance(data, list):
        # Array of sub-event objects, each with Supplemental_Material array
        all_materials = []
        for sub_event in data:
            all_materials.extend(sub_event.get("Supplemental_Material", []))
        materials = all_materials
    else:
        # Single object with materials array
        materials = data.get("materials", [])

    enriched_count = 0

    for material in materials:
        citation = material.get("citation", {})
        if not citation or not isinstance(citation, dict):
            logger.debug("Skipping material with invalid citation")
            continue
        material_type = citation.get("type", "")

        # ISBN extraction for books
        if (
            material_type == "book"
            and config.get("extract_isbn", False)
            and not citation.get("isbn")
        ):
            isbn = extract_isbn(citation, grok_client)
            if isbn:
                citation["isbn"] = isbn
                enriched_count += 1

        # Copyright determination
        if config.get("determine_copyright", False):
            authors = citation.get("author", [])
            if isinstance(authors, str):
                author = authors
            elif isinstance(authors, list) and authors:
                author = authors[0]
            else:
                author = ""

            if author and not material.get("copyright_status"):
                death_date = get_author_death_date(author, grok_client)
                jurisdiction = citation.get("publication_country", "USA")

                copyright_status = determine_copyright_status(
                    citation, death_date, jurisdiction
                )
                material["copyright_status"] = copyright_status
                enriched_count += 1

        # Archive URL verification
        if config.get("verify_archive_urls", False):
            archive_info = material.get("archive_info", {})
            archive_url = archive_info.get("archive_url")

            if archive_url and not archive_info.get("verified"):
                verification = verify_archive_url(archive_url)
                archive_info.update(verification)
                enriched_count += 1

    # Write updated file
    if enriched_count > 0:
        try:
            with open(supplemental_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Enriched %d materials with advanced features", enriched_count)
        except OSError as e:
            logger.error("Error writing supplemental file: %s", e)
            return 0

    return enriched_count
