#!/usr/bin/env python3
"""
Phase 2: Event and Entity Extraction with Grok API
"""

import argparse
import logging
from pathlib import Path
import sys

from src.utils.config import load_config, get_paths
from src.utils.logger import setup_logging
from src.grok_client import GrokClient
from src.extraction.events import extract_events
from src.extraction.dates import extract_dates
from src.extraction.places import extract_places
from src.extraction.people import extract_people
from src.extraction.people_groups import extract_people_groups
from src.extraction.external_maps import import_maps

# Import from scripts directory
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from find_duplicate_people import generate_duplicate_report
from find_related_groups import generate_related_groups_report


def main():
    """Main entry point for Phase 2."""
    parser = argparse.ArgumentParser(
        description="Extract events from parsed WWII documents"
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"],
        default=None,
        help="Set logging level (overrides config.yaml)",
    )
    args = parser.parse_args()

    # Load configuration
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Setup logging
    log_config = config.get("logging", {})
    log_level = args.log_level or log_config.get("level", "INFO")
    logger = setup_logging(
        level=log_level,
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Starting Phase 2: Event and Entity Extraction")

    # Step 0: Complete any incomplete metadata
    logger.info("\n" + "=" * 60)
    logger.info("Checking metadata completeness...")
    logger.info("=" * 60)

    try:
        from complete_metadata_with_grok import (
            is_metadata_incomplete,
            extract_metadata_with_grok,
        )
        import yaml

        content_dir = base_dir / "contentrepository"
        meta_files = list(content_dir.glob("**/*-meta.yaml"))

        incomplete_count = 0
        updated_count = 0

        for meta_file in meta_files:
            with open(meta_file, "r", encoding="utf-8") as f:
                metadata = yaml.safe_load(f)

            if is_metadata_incomplete(metadata):
                incomplete_count += 1
                logger.info(f"  Completing: {meta_file.relative_to(content_dir)}")

                # Initialize Grok client for metadata extraction
                grok_client = GrokClient(paths["api_cache"])
                updated_metadata = extract_metadata_with_grok(
                    meta_file.parent, metadata, grok_client
                )

                if updated_metadata != metadata:
                    with open(meta_file, "w", encoding="utf-8") as f:
                        yaml.dump(
                            updated_metadata,
                            f,
                            default_flow_style=False,
                            sort_keys=False,
                        )
                    logger.info(
                        f"    ✓ Updated: {updated_metadata.get('chapter_title')}"
                    )
                    updated_count += 1

        if incomplete_count > 0:
            logger.info(
                f"  Completed {updated_count}/{incomplete_count} metadata file(s)"
            )
        else:
            logger.info("  ✓ All metadata complete")

    except Exception as e:
        logger.warning(f"  Metadata completion failed: {e}")
        logger.warning("  Continuing with existing metadata...")

    # Check for API key
    from dotenv import load_dotenv

    load_dotenv()
    import os

    if not os.getenv("GROK_API_KEY"):
        logger.error("GROK_API_KEY not found in environment")
        logger.error("Please create .env file with your API key")
        logger.error("See .env.example for template")
        sys.exit(1)

    # Initialize Grok client
    cache_dir = paths["api_cache"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)
    logger.info(f"Initialized Grok client with API cache: {cache_dir}")

    # Ensure other cache directories exist
    for cache_type in ["image_cache", "map_cache"]:
        if cache_type in paths:
            paths[cache_type].mkdir(parents=True, exist_ok=True)

    # Find parsed files
    output_root = paths["output_root"]
    parsed_files = list(output_root.rglob("*-parsed.json"))

    if not parsed_files:
        logger.error(f"No parsed files found in {output_root}")
        logger.error("Please run phase1_parse.py first")
        sys.exit(1)

    logger.info(f"Found {len(parsed_files)} parsed file(s)")

    # Check concurrency config
    concurrency_enabled = config.get("concurrency", {}).get("enabled", False)
    max_workers = config.get("concurrency", {}).get("max_event_files", 3)

    if concurrency_enabled:
        logger.info(f"Concurrent processing enabled (max {max_workers} files)")
        from src.extraction.concurrent import process_files_concurrent

        # Prepare event files list
        event_files = []
        parsed_files_to_process = []

        for parsed_file in parsed_files:
            stem = parsed_file.stem.replace("-parsed", "")
            event_file = parsed_file.parent / f"{stem}-event.json"

            # Extract events if needed
            if not event_file.exists():
                logger.info(f"Extracting events: {parsed_file.name}")
                from src.extraction.events import extract_events

                output_file = extract_events(
                    parsed_file=parsed_file,
                    grok_client=grok_client,
                    output_dir=parsed_file.parent,
                )
                if output_file:
                    event_files.append(output_file)
                    parsed_files_to_process.append(parsed_file)
            else:
                event_files.append(event_file)
                parsed_files_to_process.append(parsed_file)

        # Process concurrently
        processed, failed = process_files_concurrent(
            event_files,
            parsed_files_to_process,
            grok_client,
            paths,
            config,
            max_workers,
        )
    else:
        # Sequential processing (original)
        processed = 0
        failed = 0
        for parsed_file in parsed_files:
            logger.info(f"Processing: {parsed_file.name}")

            # Check if already processed
            stem = parsed_file.stem.replace("-parsed", "")
            event_file = parsed_file.parent / f"{stem}-event.json"
            dates_file = parsed_file.parent / f"{stem}-dates.json"

            # Skip only if event and dates exist (places are in central repo)
            all_done = event_file.exists() and dates_file.exists()

            try:
                # Extract events
                if not event_file.exists():
                    output_file = extract_events(
                        parsed_file=parsed_file,
                        grok_client=grok_client,
                        output_dir=parsed_file.parent,
                    )
                    if output_file:
                        logger.info(f"  Saved: {output_file.name}")
                        processed += 1
                else:
                    output_file = event_file
                    if not all_done:
                        logger.info(f"  Using existing: {output_file.name}")

                if output_file:
                    # Extract dates from the event file (always run - central repo)
                    logger.info(f"  Extracting dates to central repository...")
                    try:
                        central_dates_dir = paths["output_root"] / "dates"
                        dates_output = extract_dates(
                            event_file=output_file,
                            grok_client=grok_client,
                            dates_dir=central_dates_dir,
                            parsed_file=parsed_file,
                        )
                        if dates_output:
                            logger.info(f"  Updated central dates repository")
                    except Exception as e:
                        logger.error(f"  Error extracting dates: {e}")

                    # Extract places from the event file (always run - central repo)
                    logger.info(f"  Extracting places to central repository...")
                    try:
                        # Use central places directory
                        central_places_dir = paths["output_root"] / "places"
                        places_output = extract_places(
                            event_file=output_file,
                            grok_client=grok_client,
                            places_dir=central_places_dir,
                            parsed_file=parsed_file,
                        )
                        if places_output:
                            logger.info(f"  Updated central places repository")
                    except Exception as e:
                        logger.error(f"  Error extracting places: {e}")

                    # Extract people (always run - handles merging internally)
                    logger.info(f"  Extracting people...")
                    try:
                        people_dir = extract_people(
                            event_file=output_file,
                            grok_client=grok_client,
                            output_dir=output_root,
                        )
                        if people_dir:
                            logger.info(f"  Updated people directory")
                    except Exception as e:
                        import traceback

                        logger.error(f"  Error extracting people: {e}")
                        logger.error(traceback.format_exc())

                    # Extract people groups (skip if already processed)
                    logger.info(f"  Extracting people groups...")
                    try:
                        groups_dir = extract_people_groups(
                            event_file=output_file,
                            grok_client=grok_client,
                            output_dir=output_root,
                        )
                        if groups_dir:
                            logger.info(f"  Updated people groups directory")
                    except Exception as e:
                        logger.error(f"  Error extracting people groups: {e}")

                    # Extract weather (if enabled)
                    if config.get("weather", {}).get("enabled", False):
                        logger.info(f"  Extracting weather to central repository...")
                        try:
                            from src.extraction.weather_central import (
                                extract_weather_central,
                            )

                            central_weather_dir = paths["output_root"] / "weather"
                            central_places_dir = paths["output_root"] / "places"
                            weather_output = extract_weather_central(
                                event_file=output_file,
                                weather_dir=central_weather_dir,
                                grok_client=grok_client,
                                places_dir=central_places_dir,
                                parsed_file=parsed_file,
                                fetch_api=config.get("weather", {}).get(
                                    "fetch_api_data", False
                                ),
                                max_retries=3,
                            )
                            if weather_output:
                                logger.info(f"  Updated central weather repository")
                        except Exception as e:
                            import traceback

                            logger.error(f"  Error extracting weather: {e}")
                            logger.error(traceback.format_exc())

                    # Extract equipment (if enabled)
                    if config.get("equipment", {}).get("enabled", False):
                        logger.info(f"  Extracting military equipment...")
                        try:
                            from src.extraction.equipment import (
                                extract_equipment_from_event,
                            )

                            equipment_dir = paths["output_root"] / "equipment"
                            enable_enrichment = config.get("equipment", {}).get(
                                "enable_enrichment", False
                            )
                            verify_media_with_vision = config.get("equipment", {}).get(
                                "verify_media_with_vision", True
                            )

                            equipment_files = extract_equipment_from_event(
                                event_file=output_file,
                                output_dir=equipment_dir,
                                grok_client=grok_client,
                                output_root=paths["output_root"],
                                enable_enrichment=enable_enrichment,
                                verify_media_with_vision=verify_media_with_vision,
                            )
                            if equipment_files:
                                logger.info(
                                    f"  Updated {len(equipment_files)} equipment file(s)"
                                )
                        except Exception as e:
                            logger.error(f"  Error extracting equipment: {e}")

                    # Extract logistics (if enabled)
                    if config.get("logistics", {}).get("enabled", False):
                        logger.info(f"  Extracting logistics issues...")
                        try:
                            from src.extraction.logistics import (
                                extract_logistics_from_event,
                            )

                            logistics_dir = extract_logistics_from_event(
                                event_file=output_file,
                                output_root=paths["output_root"],
                                grok_client=grok_client,
                            )
                            if logistics_dir:
                                logger.info(f"  Updated logistics repository")
                        except Exception as e:
                            logger.error(f"  Error extracting logistics: {e}")

                    if all_done:
                        processed += 1

            except Exception as e:
                logger.error(f"  Error processing {parsed_file.name}: {e}")
                failed += 1
                continue

    # Extract maps from Phase 1 parsed files (if enabled)
    if config.get("maps", {}).get("enabled", False):
        logger.info("\n" + "=" * 60)
        logger.info("Extracting maps from source material...")
        logger.info("=" * 60)
        try:
            from src.extraction.maps import extract_maps

            central_places_dir = paths["output_root"] / "places"
            central_dates_dir = paths["output_root"] / "dates"
            extract_maps(
                parsed_dir=output_root,
                output_dir=output_root,
                places_dir=central_places_dir,
                dates_dir=central_dates_dir,
                config=config,
            )
        except Exception as e:
            logger.error(f"  Error extracting maps: {e}")

    # Import external maps if enabled
    if config.get("external_maps", {}).get("enabled", False):
        logger.info("\n" + "=" * 60)
        logger.info("Searching for external maps...")
        logger.info("=" * 60)

        # Check if OpenSERP is available (REQUIRED)
        openserp_available = False
        openserp_error = None
        try:
            import requests

            logger.info("Checking OpenSERP availability (may take 30-60 seconds)...")
            response = requests.get(
                "http://localhost:7001/mega/search?text=test&limit=1", timeout=60
            )
            openserp_available = response.status_code == 200
            if openserp_available:
                logger.info("✓ OpenSERP detected on port 7001")
        except requests.ConnectionError as e:
            openserp_error = "connection_refused"
            logger.error(f"✗ Cannot connect to OpenSERP on port 7001")
            logger.error(f"  Error: {e}")
        except requests.Timeout:
            openserp_error = "timeout"
            logger.error(
                f"✗ OpenSERP is running but not responding (timeout after 60s)"
            )
            logger.error(f"  OpenSERP may be overloaded or misconfigured")
        except Exception as e:
            openserp_error = "unknown"
            logger.error(f"✗ OpenSERP check failed: {e}")

        if not openserp_available:
            logger.warning(
                "  OpenSERP is not available - external map search will be skipped"
            )

            if openserp_error == "connection_refused":
                logger.warning("  To enable: cd openserp && ./openserp serve -p 7001 &")
            elif openserp_error == "timeout":
                logger.warning("  OpenSERP may be overloaded or misconfigured")
                logger.warning(
                    "  Try restarting: pkill openserp && "
                    "cd openserp && ./openserp serve -p 7001 &"
                )
            else:
                logger.warning(f"  OpenSERP health check failed: {openserp_error}")

            logger.info("Continuing without external map search...")
            # Skip external maps section
            openserp_available = False

        if openserp_available:
            logger.info("Using OpenSERP for real search engine results...")
            try:
                from src.extraction.openserp_maps import import_openserp_maps

                # Get config
                external_maps_config = config.get("external_maps", {})
                max_places = external_maps_config.get("max_places", None)
                search_limit = external_maps_config.get("search_limit", 50)
                openserp_url = external_maps_config.get(
                    "openserp_url", "http://localhost:7001"
                )
                page_timeout = external_maps_config.get("page_download_timeout", 10)
                image_timeout = external_maps_config.get("image_download_timeout", 30)
                image_storage_path = external_maps_config.get("image_storage_path")

                imported = import_openserp_maps(
                    places_dir=paths["output_root"] / "places",
                    output_dir=paths["output_root"] / "external_maps",
                    grok_client=grok_client,
                    max_places=max_places,
                    search_limit=search_limit,
                    openserp_url=openserp_url,
                    page_timeout=page_timeout,
                    image_timeout=image_timeout,
                    image_storage_path=image_storage_path,
                )
                logger.info(f"  ✓ Imported {imported} maps via OpenSERP")
            except Exception as e:
                logger.error(f"  Error with OpenSERP: {e}")
                logger.warning("  Continuing without OpenSERP maps...")

        # Also import from YAML if it exists
        yaml_path = base_dir / "external_maps.yaml"
        if yaml_path.exists():
            logger.info("\n  Importing from external_maps.yaml...")
            try:
                yaml_imported = import_maps(
                    yaml_path=yaml_path,
                    output_dir=paths["output_root"] / "external_maps",
                    places_dir=paths["output_root"] / "places",
                    dates_dir=paths["output_root"] / "dates",
                    storage_backend=config.get("external_maps", {}).get(
                        "storage_backend", "filesystem"
                    ),
                    allowed_licenses=config.get("external_maps", {}).get(
                        "allowed_licenses"
                    ),
                )
                logger.info(f"  ✓ Imported {yaml_imported} maps from YAML")
            except Exception as e:
                logger.error(f"  Error importing from YAML: {e}")

    # Validate all files have event files
    logger.info("\n" + "=" * 60)
    logger.info("Validating event file generation...")
    logger.info("=" * 60)

    missing_events = []
    for parsed_file in sorted(parsed_files):
        event_file = parsed_file.parent / parsed_file.name.replace(
            "-parsed.json", "-event.json"
        )
        if not event_file.exists():
            missing_events.append(parsed_file)

    if missing_events:
        logger.warning(f"\n{len(missing_events)} file(s) missing event files")

        # Retry missing files with cache cleared
        logger.info("\nRetrying failed files with cache cleared...")
        for parsed_file in missing_events:
            logger.info(f"Retrying: {parsed_file.name}")

            # Clear cache for this specific file by clearing entire events cache
            # (simpler than trying to match specific keys)
            cache = grok_client._get_cache("events")
            cache.clear()
            logger.info("  Cleared events cache")

            try:
                output_file = extract_events(
                    parsed_file=parsed_file,
                    grok_client=grok_client,
                    output_dir=parsed_file.parent,
                )
                if output_file:
                    logger.info(f"  ✓ Successfully generated: {output_file.name}")
                    processed += 1
                else:
                    logger.warning(f"  ⏭ Skipped (footnotes only)")
            except Exception as e:
                logger.error(f"  ✗ Retry failed: {e}")
                failed += 1

    logger.info("\n" + "=" * 60)
    logger.info(f"Phase 2 complete!")
    logger.info(f"Processed: {processed}, Failed: {failed}")
    logger.info("=" * 60)

    # Find potential duplicates
    logger.info("\n" + "=" * 60)
    logger.info("Analyzing for duplicate people...")
    logger.info("=" * 60)

    people_dir = output_root / "people"

    if people_dir.exists():
        try:
            duplicate_report = people_dir / "duplicate_report.json"
            generate_duplicate_report(people_dir, duplicate_report)
            logger.info(f"  ✓ Saved: {duplicate_report.name}")
        except Exception as e:
            logger.error(f"  ✗ Duplicate detection failed: {e}")
    else:
        logger.warning("No people directory found")

    # Generate related groups report
    logger.info("\n" + "=" * 60)
    logger.info("Analyzing people groups for relationships...")
    logger.info("=" * 60)

    groups_dir = output_root / "people_groups"

    if groups_dir.exists():
        try:
            related_report = groups_dir / "related_groups_report.json"
            generate_related_groups_report(groups_dir, related_report)
            logger.info(f"  ✓ Saved: {related_report.name}")
        except Exception as e:
            logger.error(f"  ✗ Related groups analysis failed: {e}")
    else:
        logger.warning("No people groups directory found")

    logger.info("\n" + "=" * 60)
    logger.info("All processing complete!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
