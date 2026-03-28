#!/usr/bin/env python3
"""
Enrich extracted data from external sources.

Runs enrichment for various entity types (people, places, etc.)
by searching external sources and merging additional data.
"""

import argparse
from pathlib import Path

from src.extraction.enrich_biographies import enrich_all_people
from src.extraction.enrich_groups import enrich_all_groups
from src.extraction.enrich_places import enrich_all_places
from src.extraction.places import link_parent_place_ids
from src.extraction.supplemental_advanced import enrich_bibliography
from src.grok_client import GrokClient
from src.utils.config import load_config
from src.utils.logger import setup_logging

import logging

logger = logging.getLogger(__name__)


def enrich_people_data(
    people_dir: Path,
    grok_client: GrokClient,
    max_items: int = None,
    search_references: bool = True,
) -> int:
    """Enrich people biographical data from Grokipedia and Wikipedia."""
    logger.info("\n" + "=" * 80)
    logger.info("ENRICHING PEOPLE")
    logger.info("=" * 80)

    if not people_dir.exists():
        logger.warning(f"People directory not found: {people_dir}")
        return 0

    people_files = [
        f
        for f in people_dir.glob("*.json")
        if f.name not in ["index.json", "duplicate_report.json", "not_duplicates.json"]
    ]

    if not people_files:
        logger.info("No people files found")
        return 0

    logger.info(f"Found {len(people_files)} people file(s)")
    if max_items:
        logger.info(f"Limiting to {max_items} people")

    enriched = enrich_all_people(
        people_dir,
        grok_client,
        max_people=max_items,
        search_references_flag=search_references,
    )

    logger.info(f"✓ Enriched {enriched} people")
    return enriched


def enrich_groups_data(
    groups_dir: Path, grok_client: GrokClient, max_items: int = None
) -> int:
    """Enrich people groups with external data."""
    logger.info("\n" + "=" * 80)
    logger.info("ENRICHING PEOPLE GROUPS")
    logger.info("=" * 80)

    enriched = enrich_all_groups(groups_dir, grok_client, max_groups=max_items)
    logger.info(f"✓ Enriched {enriched} groups")
    return enriched


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enrich extracted data from external sources"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory containing entity subdirectories (default: output)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/grok_cache"),
        help="Cache directory for Grok API (default: cache/grok_cache)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum items per entity type to enrich (default: all)",
    )
    parser.add_argument(
        "--no-references",
        action="store_true",
        help="Don't follow references (faster, less complete)",
    )
    parser.add_argument(
        "--people-only",
        action="store_true",
        help="Only enrich people (skip other entity types)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Load config for logging settings
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    log_config = config.get("logging", {})

    # Setup logging with file output
    logger = setup_logging(
        level=args.log_level,
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    if not args.output_dir.exists():
        logger.error(f"Output directory not found: {args.output_dir}")
        return 1

    logger.info("Starting enrichment process...")
    if args.max_items:
        logger.info(f"Limiting to {args.max_items} items per entity type")
    if args.no_references:
        logger.info("Reference following disabled")

    # Initialize Grok client
    grok_client = GrokClient(args.cache_dir)

    total_enriched = 0

    # Enrich people
    people_dir = args.output_dir / "people"
    people_enriched = enrich_people_data(
        people_dir,
        grok_client,
        max_items=args.max_items,
        search_references=not args.no_references,
    )
    total_enriched += people_enriched

    # Enrich people groups
    if not args.people_only:
        groups_dir = args.output_dir / "people_groups"
        groups_enriched = enrich_groups_data(
            groups_dir, grok_client, max_items=args.max_items
        )
        total_enriched += groups_enriched

    # Enrich places
    if not args.people_only:
        places_dir = args.output_dir / "places"
        places_enriched = enrich_all_places(
            places_dir, grok_client, max_places=args.max_items
        )
        total_enriched += places_enriched

        # Link parent_place_id after enrichment populates hierarchy
        link_parent_place_ids(places_dir)

    # Enrich bibliography (ISBN, copyright, archive URLs)
    if not args.people_only:
        bib_dir = args.output_dir / "bibliography"
        supplemental_config = config.get("supplemental_material", {})
        bib_enriched = enrich_bibliography(bib_dir, supplemental_config, grok_client)
        total_enriched += bib_enriched

    logger.info("\n" + "=" * 80)
    logger.info(f"ENRICHMENT COMPLETE: {total_enriched} total items enriched")
    logger.info("=" * 80)

    from src.utils.http_pool import close_session

    close_session()

    return 0


if __name__ == "__main__":
    exit(main())
