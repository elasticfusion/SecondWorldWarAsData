"""Copyright calculation based on author death date and country laws."""

from datetime import datetime
from typing import Optional, Tuple

# Copyright duration by country (years after author's death)
COPYRIGHT_DURATION = {
    "USA": 70,
    "CAN": 70,
    "GBR": 70,
    "FRA": 70,  # Economic rights; moral rights are perpetual
    "DEU": 70,
}


def parse_death_year(death_date: Optional[str]) -> Optional[int]:
    """Extract year from death date string (YYYY or YYYY-MM-DD)."""
    if not death_date or death_date == "UNKNOWN":
        return None

    try:
        # Handle YYYY-MM-DD or YYYY
        return int(death_date.split("-")[0])
    except (ValueError, AttributeError):
        return None


def calculate_copyright_expiration(
    author_death_date: Optional[str],
    publication_country: Optional[str],
    publication_date: Optional[str] = None,
) -> Tuple[Optional[int], str, str]:
    """
    Calculate copyright expiration year.

    Returns:
        tuple: (expiration_year, license, license_notes)
    """
    death_year = parse_death_year(author_death_date)
    country = publication_country or "USA"
    current_year = datetime.now().year

    # Unknown death date
    if death_year is None:
        if publication_date and publication_date != "UNKNOWN":
            try:
                pub_year = int(publication_date.split("-")[0])
                # US works published before 1928 are public domain
                if country == "USA" and pub_year < 1928:
                    return (1928, "public_domain", "Published before 1928 (US)")
            except (ValueError, AttributeError):
                pass

        return (None, "unknown", "Author death date unknown")

    # Calculate expiration
    duration = COPYRIGHT_DURATION.get(country, 70)
    expiration_year = death_year + duration

    if current_year >= expiration_year:
        license_status = "public_domain"
        notes = f"Copyright expired {expiration_year} (author death {death_year} + {duration} years)"
    else:
        license_status = "copyright"
        notes = f"Copyright expires {expiration_year} (author death {death_year} + {duration} years)"

    return (expiration_year, license_status, notes)


def determine_license(material: dict) -> Tuple[str, str]:
    """
    Determine license for supplemental material.

    Returns:
        tuple: (license, license_notes)
    """
    citation = material.get("citation", {})

    # Government/educational institutions
    publisher = citation.get("publisher", "") or ""
    if any(
        keyword in publisher.lower()
        for keyword in [
            "government",
            "army",
            "navy",
            "department",
            "office",
            "national archives",
        ]
    ):
        return ("public_domain", "US Government publication")

    # Gutenberg.org materials
    urls = material.get("resource_urls", [])
    if any("gutenberg.org" in url for url in urls):
        return ("public_domain", "Project Gutenberg")

    # Calculate based on author death date
    author_death_date = citation.get("author_death_date")
    publication_country = citation.get("publication_country")
    publication_date = citation.get("publication_date")

    if author_death_date:
        _, license_status, notes = calculate_copyright_expiration(
            author_death_date, publication_country, publication_date
        )
        return (license_status, notes)

    # Default: unknown
    return ("unknown", "Insufficient information for copyright determination")
