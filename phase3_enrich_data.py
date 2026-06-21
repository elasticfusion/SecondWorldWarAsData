#!/usr/bin/env python3
"""
Enrich extracted data from external sources.

Runs enrichment for various entity types (people, places, etc.)
by searching external sources and merging additional data.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Optional

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


def _notify_enrichment_started() -> None:
    """Send SNS notification that enrichment is starting (downloads complete)."""
    if not os.environ.get("ECS_CONTAINER_METADATA_URI"):
        return  # Local mode
    try:
        import boto3

        topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
        if not topic_arn:
            return
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        book = os.environ.get("BOOK_NAME", "all")
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic_arn,
            Subject="WWII Pipeline: Phase 3 enrichment in progress",
            Message=f"Enrichment started (downloads complete, API calls beginning).\nBook: {book}",
        )
    except Exception:
        pass


def _update_lock_status(status: str) -> None:
    """Update the Phase 3 lock with current enrichment status (best-effort)."""
    if not os.environ.get("ECS_CONTAINER_METADATA_URI"):
        return  # Local mode — no lock
    try:
        import boto3

        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        env_name = os.environ.get("ENV_NAME", "dev")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        table.update_item(
            Key={"cache_key": f"lock#{env_name}-wwii-phase3-enrich"},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )
    except Exception:
        pass


def enrich_people_data(
    people_dir: Path,
    grok_client: GrokClient,
    max_items: Optional[int] = None,
    search_references: bool = True,
    max_workers: int = 6,
) -> int:
    """Enrich people biographical data from Grokipedia and Wikipedia."""
    logger.info("[phase3 step 1/6] Enriching people (%s)", people_dir)
    _update_lock_status("step 1/6: enriching people")

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
        max_workers=max_workers,
    )

    logger.info(f"✓ Enriched {enriched} people")
    return enriched


def enrich_groups_data(
    groups_dir: Path,
    grok_client: GrokClient,
    max_items: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich people groups with external data."""
    logger.info("[phase3 step 2/6] Enriching people groups")
    _update_lock_status("step 2/6: enriching people_groups")

    enriched = enrich_all_groups(
        groups_dir, grok_client, max_groups=max_items, max_workers=max_workers
    )
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
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use xAI Batch API (50%% cost reduction, async processing)",
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

    logger.info("Phase 3: starting enrichment (%s)", args.output_dir)

    # Notify: enrichment is starting (downloads complete, real work beginning)
    _notify_enrichment_started()

    if args.max_items:
        logger.info(f"Limiting to {args.max_items} items per entity type")
    if args.no_references:
        logger.info("Reference following disabled")

    # Initialize Grok client
    grok_client = GrokClient(args.cache_dir, batch_mode=args.batch)
    if args.batch:
        logger.info(
            "Batch mode enabled — collecting requests for xAI Batch API (50%% off)"
        )

    total_enriched = 0
    max_workers = config.get("concurrency", {}).get("max_enrichment_workers", 6)

    # Enrich people
    people_dir = args.output_dir / "people"
    people_enriched = enrich_people_data(
        people_dir,
        grok_client,
        max_items=args.max_items,
        search_references=not args.no_references,
        max_workers=max_workers,
    )
    total_enriched += people_enriched

    # Enrich people groups
    if not args.people_only:
        groups_dir = args.output_dir / "people_groups"
        groups_enriched = enrich_groups_data(
            groups_dir, grok_client, max_items=args.max_items, max_workers=max_workers
        )
        total_enriched += groups_enriched

    # Enrich places
    if not args.people_only:
        logger.info("[phase3 step 3/6] Enriching places")
        _update_lock_status("step 3/6: enriching places")
        places_dir = args.output_dir / "places"
        places_enriched = enrich_all_places(
            places_dir, grok_client, max_places=args.max_items, max_workers=max_workers
        )
        total_enriched += places_enriched

        # Link parent_place_id after enrichment populates hierarchy
        link_parent_place_ids(places_dir)

    # Enrich bibliography (ISBN, copyright, archive URLs)
    if not args.people_only:
        logger.info("[phase3 step 4/6] Enriching bibliography")
        _update_lock_status("step 4/6: enriching bibliography")
        bib_dir = args.output_dir / "bibliography"
        supplemental_config = config.get("supplemental_material", {})
        bib_enriched = enrich_bibliography(bib_dir, supplemental_config, grok_client)
        total_enriched += bib_enriched

        # Resolve bibliography sources (NARA, Archive.org, LOC)
        from src.enrichment.bibliography_resolver import resolve_bibliography_dir

        resolve_config = {
            "nara_api_key": config.get("api", {}).get("nara_api_key"),
            "search_gutenberg": supplemental_config.get("search_gutenberg", True),
            "search_archive_org": supplemental_config.get("search_archive_org", True),
            "use_openserp": supplemental_config.get("use_openserp", False),
            "openserp_url": config.get("external_maps", {}).get(
                "openserp_url", "http://localhost:7001"
            ),
        }
        resolve_stats = resolve_bibliography_dir(
            bib_dir, grok_client, resolve_config, max_items=args.max_items
        )
        total_enriched += resolve_stats["resolved"]

    # Equipment Wikipedia enrichment (images + extracts)
    if not args.people_only and config.get("equipment", {}).get("enabled"):
        logger.info("[phase3 step 4b/6] Equipment Wikipedia enrichment")
        _update_lock_status("step 4b/6: enriching equipment (Wikipedia)")
        from src.enrichment.equipment_wikipedia import enrich_all_equipment_wikipedia

        equipment_dir = args.output_dir / "equipment"
        if equipment_dir.exists():
            total_enriched += enrich_all_equipment_wikipedia(
                equipment_dir, max_items=args.max_items
            )

    # Groups Wikipedia enrichment (images + extracts)
    if not args.people_only:
        logger.info("[phase3 step 4c/6] Groups Wikipedia enrichment")
        _update_lock_status("step 4c/6: enriching groups (Wikipedia)")
        from src.enrichment.groups_wikipedia import enrich_all_groups_wikipedia

        groups_dir = args.output_dir / "people_groups"
        if groups_dir.exists():
            total_enriched += enrich_all_groups_wikipedia(
                groups_dir, max_items=args.max_items
            )

    # OpenSERP enrichment (images, academic sources) — requires OpenSERP running
    if not args.people_only and config.get("supplemental_material", {}).get(
        "use_openserp", False
    ):
        logger.info("[phase3 step 5/6] OpenSERP enrichment (people + equipment)")
        _update_lock_status("step 5/6: enriching openserp")
        from src.enrichment.openserp_enrichment import (
            enrich_equipment_with_openserp,
            enrich_people_with_openserp,
        )

        openserp_url = config.get("external_maps", {}).get(
            "openserp_url", "http://localhost:7001"
        )
        total_enriched += enrich_people_with_openserp(
            args.output_dir / "people", openserp_url, grok_client, args.max_items
        )
        total_enriched += enrich_equipment_with_openserp(
            args.output_dir / "equipment", openserp_url, grok_client, args.max_items
        )

    # NOAA weather enrichment (observed data to supplement Open-Meteo)
    noaa_token = config.get("api", {}).get("noaa_api_token", "")
    if not args.people_only and noaa_token:
        logger.info("[phase3 step 6/6] NOAA weather enrichment")
        _update_lock_status("step 6/6: enriching weather (NOAA)")
        from src.enrichment.noaa_weather import enrich_weather_with_noaa

        weather_dir = args.output_dir / "weather"
        if weather_dir.exists():
            total_enriched += enrich_weather_with_noaa(
                weather_dir, noaa_token, args.max_items or 0
            )

    logger.info("Phase 3 complete: %d total items enriched", total_enriched)
    grok_client.log_cache_stats()

    # If batch mode, submit collected requests, wait, then re-run
    if (
        args.batch
        and grok_client._batch_collector
        and len(grok_client._batch_collector) > 0
    ):
        logger.info(
            "Submitting %d requests to xAI Batch API (50%% off)...",
            len(grok_client._batch_collector),
        )
        book = os.environ.get("BOOK_NAME", "all")
        batch_id = grok_client.submit_batch(
            batch_name=f"phase3-{book}-{len(grok_client._batch_collector)}reqs"[:128]
        )
        if batch_id:
            logger.info("Batch complete! Re-running enrichment with cached results...")
            grok_client.batch_mode = False
            total_enriched = 0

            people_enriched = enrich_people_data(
                people_dir,
                grok_client,
                max_items=args.max_items,
                search_references=not args.no_references,
            )
            total_enriched += people_enriched

            if not args.people_only:
                groups_dir = args.output_dir / "people_groups"
                places_dir = args.output_dir / "places"
                bib_dir = args.output_dir / "bibliography"
                supplemental_config = config.get("supplemental_material", {})
                total_enriched += enrich_groups_data(
                    groups_dir, grok_client, max_items=args.max_items
                )
                total_enriched += enrich_all_places(
                    places_dir, grok_client, max_places=args.max_items
                )
                link_parent_place_ids(places_dir)
                total_enriched += enrich_bibliography(
                    bib_dir, supplemental_config, grok_client
                )

                # Resolve bibliography sources (NARA, Archive.org, LOC)
                from src.enrichment.bibliography_resolver import (
                    resolve_bibliography_dir,
                )

                resolve_config = {
                    "nara_api_key": config.get("api", {}).get("nara_api_key"),
                    "search_gutenberg": supplemental_config.get(
                        "search_gutenberg", True
                    ),
                    "search_archive_org": supplemental_config.get(
                        "search_archive_org", True
                    ),
                    "use_openserp": supplemental_config.get("use_openserp", False),
                    "openserp_url": config.get("external_maps", {}).get(
                        "openserp_url", "http://localhost:7001"
                    ),
                }
                resolve_stats = resolve_bibliography_dir(
                    bib_dir, grok_client, resolve_config, max_items=args.max_items
                )
                total_enriched += resolve_stats["resolved"]

            logger.info(
                "[phase3] Batch re-run complete: %d total items enriched",
                total_enriched,
            )

    from src.utils.http_pool import close_session

    close_session()

    # Write results for entrypoint notification
    results_file = args.output_dir / ".phase_results.json"
    entity_counts = {}
    for subdir in [
        "people",
        "people_groups",
        "places",
        "dates",
        "equipment",
        "weather",
        "logistics",
        "casualties",
        "maps",
        "supplemental",
    ]:
        d = args.output_dir / subdir
        if d.exists():
            entity_counts[subdir] = len(
                [f for f in d.glob("*.json") if f.name != "index.json"]
            )
    results_file.write_text(
        json.dumps(
            {
                "enriched": total_enriched,
                "entity_counts": entity_counts,
            }
        ),
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    exit(main())
