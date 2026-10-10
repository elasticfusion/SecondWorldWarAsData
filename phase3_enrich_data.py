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
    except Exception as e:  # noqa: BLE001 - best-effort notify, but DON'T go silent
        logger.warning("Could not send enrichment-started notification: %s", e)


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
    except Exception as e:  # noqa: BLE001 - best-effort status, but DON'T go silent
        logger.warning("Could not update Phase 3 lock status '%s': %s", status, e)


# NOTE: people Grokipedia/Wikipedia enrichment MOVED TO PHASE 2 (people.py
# _enrich_person_phase2 -> enrich_biographies.enrich_person_from_sources). The former
# Phase-3 enrich_people_data() has been removed. Phase-3 people enrichment is OpenSERP-only
# (see the openserp_people step in main()). enrich_all_people remains in enrich_biographies
# for the module CLI + integration tests, but is intentionally NOT wired into this pipeline.


def enrich_groups_data(
    groups_dir: Path,
    grok_client: GrokClient,
    max_items: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich people groups with external data."""
    logger.info("[phase3 step 1/6] Enriching people groups")
    _update_lock_status("step 1/6: enriching people_groups")

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

    from src.utils.validation_stats import reset_stats as _reset_val_stats

    _reset_val_stats()

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

    # C3: per-source enrichment stats so "complete" reflects partial success +
    # previously-swallowed failures surface to the operator (via .phase_results.json
    # -> completion notification -> email + Slack). C2: a source that throws is
    # LOGGED + recorded as errored, never silently swallowed and never aborts the
    # rest of Phase 3.
    source_stats: dict = {}

    def _run_step(name: str, fn) -> int:
        """Run one enrichment source; record enriched/status/error. Returns the
        enriched count (0 on error). Never raises — a failed source is captured,
        logged, and surfaced, not swallowed."""
        try:
            result = fn()
            count = (
                result
                if isinstance(result, int)
                else getattr(result, "geocoded", 0) or 0
            )
            source_stats[name] = {"enriched": count, "status": "ok"}
            return count
        except Exception as e:  # noqa: BLE001 - capture + surface, don't abort Phase 3
            logger.error("Enrichment source '%s' FAILED: %s", name, e, exc_info=True)
            source_stats[name] = {
                "enriched": 0,
                "status": "error",
                "error": str(e)[:300],
            }
            return 0

    # Enrich people — MOVED TO PHASE 2. All Grokipedia/Wikipedia people enrichment
    # (bio text + Wikipedia portrait images + award citations) is now committed
    # natively by the Phase-2 people extractor (src/extraction/people.py ->
    # enrich_biographies.enrich_person_from_sources). Phase 3 owns only OTHER external
    # people enrichment (OpenSERP, below). The former '[phase3 step 1/6] Enriching
    # people' call (enrich_people_data/enrich_all_people) is intentionally removed;
    # those functions remain defined for standalone/CLI + test use.

    # Enrich people groups — STRUCTURED org-history facts (unit_type, nationality,
    # commanding officers, operations) via a Grok extract_json, promoted into spec
    # fields. This is the groups analogue of place hierarchy/names: structured
    # reference facts, NOT descriptive Grokipedia/Wikipedia text+images. The latter
    # (wikipedia_url/extract/images) moved to PHASE 2 (people_groups extractor →
    # enrich_group_from_wikipedia). This structured-facts enrichment therefore stays
    # in Phase 3, mirroring the rule that place hierarchy/names remain in Phase 3.
    if not args.people_only:
        groups_dir = args.output_dir / "people_groups"
        total_enriched += _run_step(
            "people_groups",
            lambda: enrich_groups_data(
                groups_dir,
                grok_client,
                max_items=args.max_items,
                max_workers=max_workers,
            ),
        )

    # Enrich places
    if not args.people_only:
        logger.info("[phase3 step 2/6] Enriching places")
        _update_lock_status("step 2/6: enriching places")
        places_dir = args.output_dir / "places"
        total_enriched += _run_step(
            "places",
            lambda: enrich_all_places(
                places_dir,
                grok_client,
                max_places=args.max_items,
                max_workers=max_workers,
            ),
        )

        # Link parent_place_id after enrichment populates hierarchy
        link_parent_place_ids(places_dir)

        # Geocode places (C1 fix): enrich_all_places sets hierarchy/names but NOT
        # coordinates — the geocoding cascade was built + tested but never wired in,
        # leaving every place at lat/long=0.0 with empty country. Wire it here:
        # Nominatim first (free, cached, policy-compliant) -> hill/terrain geocoder
        # for height features -> Grok default for the rest. Writes coordinates +
        # geocode provenance; low-confidence hits are surfaced + flagged, not
        # fabricated.
        logger.info("[phase3 step 3/6] Geocoding places (coordinates)")
        _update_lock_status("step 3/6: geocoding places")
        try:
            from src.enrichment.hill_geocode import make_hill_geocoder
            from src.enrichment.nominatim_geocode import make_nominatim_geocoder
            from src.enrichment.places_grok_geocode import (
                cascade_geocoder,
                geocode_place,
                geocode_places_dir,
            )

            geo_cache = args.cache_dir / "geocode"
            cascade = cascade_geocoder(
                make_nominatim_geocoder(geo_cache / "nominatim"),
                make_hill_geocoder(geo_cache / "elevation"),
                geocode_place,  # Grok fallback for misses
            )
            geo_report = geocode_places_dir(
                places_dir,
                grok_client,
                write=True,
                limit=args.max_items or None,
                geocoder=cascade,
            )
            logger.info(
                "Geocoding: attempted %d, geocoded %d, not-found %d, low-conf %d, "
                "errors %d",
                geo_report.attempted,
                geo_report.geocoded,
                geo_report.not_found,
                geo_report.low_confidence,
                geo_report.errors,
            )
            total_enriched += geo_report.geocoded
            # C3: record the full geo breakdown (attempted/geocoded/not_found/
            # low_confidence/errors) so partial geocoding success is visible.
            source_stats["geocode"] = {
                "enriched": geo_report.geocoded,
                "status": "error" if geo_report.errors else "ok",
                "attempted": geo_report.attempted,
                "not_found": geo_report.not_found,
                "low_confidence": geo_report.low_confidence,
                "errors": geo_report.errors,
            }
        except Exception as e:  # noqa: BLE001 - geocoding must not abort Phase 3
            logger.error(
                "Geocoding step FAILED (places left un-geocoded): %s", e, exc_info=True
            )
            source_stats["geocode"] = {
                "enriched": 0,
                "status": "error",
                "error": str(e)[:300],
            }

    # Enrich bibliography (ISBN, copyright, archive URLs)
    if not args.people_only:
        logger.info("[phase3 step 4/6] Enriching bibliography")
        _update_lock_status("step 4/6: enriching bibliography")
        bib_dir = args.output_dir / "bibliography"
        supplemental_config = config.get("supplemental_material", {})
        total_enriched += _run_step(
            "bibliography",
            lambda: enrich_bibliography(bib_dir, supplemental_config, grok_client),
        )

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
        total_enriched += _run_step(
            "bibliography_resolve",
            lambda: resolve_bibliography_dir(
                bib_dir, grok_client, resolve_config, max_items=args.max_items
            ).get("resolved", 0),
        )

    # Equipment Wikipedia enrichment (text + images) now runs in PHASE 2, committed
    # natively by the extractor (src/extraction/equipment.py). It is intentionally NOT a
    # Phase-3 step anymore: Phase 2 owns ALL Grokipedia/Wikipedia enrichment; Phase 3
    # owns only OTHER external enrichment (geocoding, NOAA, OpenSERP, NARA).

    # Groups Wikipedia enrichment (text + images) now runs in PHASE 2, committed
    # natively by the extractor (src/extraction/people_groups.py). It is intentionally
    # NOT a Phase-3 step anymore: Phase 2 owns ALL Grokipedia/Wikipedia enrichment;
    # Phase 3 owns only OTHER external enrichment (geocoding, NOAA, OpenSERP, NARA).

    # OpenSERP enrichment (images, academic sources) — requires OpenSERP running
    if not args.people_only and config.get("supplemental_material", {}).get(
        "use_openserp", False
    ):
        logger.info("[phase3 step 5/6] OpenSERP enrichment (people + equipment)")
        _update_lock_status("step 5/6: enriching openserp")
        from src.enrichment.openserp_enrichment import (
            enrich_equipment_with_openserp,
            enrich_groups_with_openserp,
            enrich_people_with_openserp,
            enrich_places_with_openserp,
            enrich_source_sections_with_openserp,
            reset_metrics as _reset_openserp_metrics,
            write_metrics as _write_openserp_metrics,
        )

        _reset_openserp_metrics()
        openserp_url = config.get("external_maps", {}).get(
            "openserp_url", "http://localhost:7001"
        )
        total_enriched += _run_step(
            "openserp_people",
            lambda: enrich_people_with_openserp(
                args.output_dir / "people", openserp_url, grok_client, args.max_items
            ),
        )
        total_enriched += _run_step(
            "openserp_equipment",
            lambda: enrich_equipment_with_openserp(
                args.output_dir / "equipment", openserp_url, grok_client, args.max_items
            ),
        )
        total_enriched += _run_step(
            "openserp_groups",
            lambda: enrich_groups_with_openserp(
                args.output_dir / "people_groups",
                openserp_url,
                grok_client,
                args.max_items,
            ),
        )
        total_enriched += _run_step(
            "openserp_places",
            lambda: enrich_places_with_openserp(
                args.output_dir / "places",
                openserp_url,
                grok_client,
                args.max_items,
            ),
        )
        total_enriched += _run_step(
            "openserp_source_sections",
            lambda: enrich_source_sections_with_openserp(
                args.output_dir / "source_section",
                openserp_url,
                grok_client,
                args.max_items,
            ),
        )
        # Durable OpenSERP effectiveness/health metrics -> output/metrics/openserp_metrics.json
        _write_openserp_metrics(args.output_dir)

    # NOAA weather enrichment (observed data to supplement Open-Meteo)
    noaa_token = config.get("api", {}).get("noaa_api_token", "")
    if not args.people_only and noaa_token:
        logger.info("[phase3 step 6/6] NOAA weather enrichment")
        _update_lock_status("step 6/6: enriching weather (NOAA)")
        from src.enrichment.noaa_weather import enrich_weather_with_noaa

        weather_dir = args.output_dir / "weather"
        if weather_dir.exists():
            total_enriched += _run_step(
                "noaa_weather",
                lambda: enrich_weather_with_noaa(
                    weather_dir, noaa_token, args.max_items or 0
                ),
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

            # People enrichment MOVED TO PHASE 2 (not re-run here).

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
    errored_sources = sorted(
        n for n, s in source_stats.items() if s.get("status") == "error"
    )
    if errored_sources:
        logger.error(
            "Phase 3: %d enrichment source(s) FAILED: %s",
            len(errored_sources),
            ", ".join(errored_sources),
        )
    results_file.write_text(
        json.dumps(
            {
                "enriched": total_enriched,
                "entity_counts": entity_counts,
                # C3: per-source stats + explicit failure list so "complete"
                # reflects partial success and previously-swallowed failures reach
                # the operator via the completion notification (email + Slack).
                "source_stats": source_stats,
                "errored_sources": errored_sources,
            }
        ),
        encoding="utf-8",
    )

    from src.utils.validation_stats import write_validation_stats as _write_val_stats

    _write_val_stats(args.output_dir)
    try:
        from src.utils.referential_integrity import run_for_phase as _ref_enforce

        _ref_enforce(args.output_dir, phase="Phase 3")
    except Exception as _e:  # enforcement must never crash a phase
        logger.warning("referential-integrity enforcement skipped: %s", _e)

    return 0


if __name__ == "__main__":
    exit(main())
