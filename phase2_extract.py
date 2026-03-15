#!/usr/bin/env python3
"""
Phase 2: Event and Entity Extraction with Grok API
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from src.utils.config import load_config, get_paths
from src.utils.logger import setup_logging
from src.grok_client import GrokClient
from src.extraction.events import extract_events
from src.extraction.external_maps import import_maps

# Import from scripts directory
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
# pylint: disable=wrong-import-order,wrong-import-position
from find_duplicate_people import generate_duplicate_report
from find_related_groups import generate_related_groups_report

# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _complete_metadata(base_dir, paths, logger):
    """Step 0: Complete any incomplete metadata."""
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
                logger.info("  Completing: %s", meta_file.relative_to(content_dir))

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
                        "    ✓ Updated: %s", updated_metadata.get("chapter_title")
                    )
                    updated_count += 1

        if incomplete_count > 0:
            logger.info(
                "  Completed %d/%d metadata file(s)", updated_count, incomplete_count
            )
        else:
            logger.info("  ✓ All metadata complete")

    except Exception as e:
        logger.warning("  Metadata completion failed: %s", e)
        logger.warning("  Continuing with existing metadata...")


def _extract_optional_entities(
    event_file, parsed_file, grok_client, paths, config, logger
):
    """Run optional entity extractors (weather, equipment, logistics, casualties, supplemental) for a single event file."""
    output_root = paths["output_root"]

    _extract_weather(event_file, parsed_file, grok_client, output_root, config, logger)
    _extract_equipment(event_file, grok_client, output_root, config, logger)
    _extract_logistics(event_file, grok_client, output_root, config, logger)
    _extract_casualties(event_file, grok_client, output_root, config, logger)
    _extract_supplemental(event_file, grok_client, output_root, config, logger)


def _extract_weather(event_file, parsed_file, grok_client, output_root, config, logger):
    if not config.get("weather", {}).get("enabled", False):
        return
    try:
        from src.extraction.weather_central import extract_weather_central

        result = extract_weather_central(
            event_file=event_file,
            weather_dir=output_root / "weather",
            grok_client=grok_client,
            places_dir=output_root / "places",
            parsed_file=parsed_file,
            fetch_api=config.get("weather", {}).get("fetch_api_data", False),
            max_retries=3,
        )
        if result:
            logger.info("    ✓ Weather updated")
    except ValueError as e:
        logger.error("    Weather config error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Weather file I/O error for %s: %s", event_file.name, e)
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting weather from %s: %s", event_file.name, e)


def _extract_equipment(event_file, grok_client, output_root, config, logger):
    if not config.get("equipment", {}).get("enabled", False):
        return
    try:
        from src.extraction.equipment import extract_equipment_from_event

        equipment_files = extract_equipment_from_event(
            event_file=event_file,
            output_dir=output_root / "equipment",
            grok_client=grok_client,
            output_root=output_root,
            enable_enrichment=config.get("equipment", {}).get(
                "enable_enrichment", False
            ),
            verify_media_with_vision=config.get("equipment", {}).get(
                "verify_media_with_vision", True
            ),
        )
        if equipment_files:
            logger.info("    ✓ Equipment: %d file(s)", len(equipment_files))
    except json.JSONDecodeError as e:
        logger.error("    Equipment JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Equipment file I/O error for %s: %s", event_file.name, e)
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting equipment from %s: %s", event_file.name, e)


def _extract_logistics(event_file, grok_client, output_root, config, logger):
    if not config.get("logistics", {}).get("enabled", False):
        return
    try:
        from src.extraction.logistics import extract_logistics_from_event

        result = extract_logistics_from_event(
            event_file=event_file,
            output_root=output_root,
            grok_client=grok_client,
        )
        if result:
            logger.info("    ✓ Logistics updated")
    except json.JSONDecodeError as e:
        logger.error("    Logistics JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Logistics file I/O error for %s: %s", event_file.name, e)
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting logistics from %s: %s", event_file.name, e)


def _extract_casualties(event_file, grok_client, output_root, config, logger):
    if not config.get("casualties", {}).get("enabled", False):
        return
    try:
        from src.extraction.casualties import extract_casualties

        casualties = extract_casualties(
            event_file=event_file,
            output_root=output_root,
            grok_client=grok_client,
        )
        if casualties:
            logger.info("    ✓ Casualties: %d record(s)", len(casualties))
    except json.JSONDecodeError as e:
        logger.error("    Casualties JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Casualties file I/O error for %s: %s", event_file.name, e)
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting casualties from %s: %s", event_file.name, e)


def _extract_supplemental(event_file, grok_client, output_root, config, logger):
    if not config.get("supplemental_material", {}).get("enabled", False):
        return
    try:
        from src.extraction.supplemental import extract_supplemental

        supplemental_dir = output_root / "supplemental"
        supplemental_dir.mkdir(parents=True, exist_ok=True)
        result = extract_supplemental(
            event_file=event_file,
            grok_client=grok_client,
            output_dir=supplemental_dir,
            output_root=output_root,
        )
        if result:
            logger.info("    ✓ Supplemental updated")
    except json.JSONDecodeError as e:
        logger.error("    Supplemental JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Supplemental file I/O error for %s: %s", event_file.name, e)
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            "    Error extracting supplemental from %s: %s", event_file.name, e
        )


def _extract_maps(output_root, config, logger):
    """Extract maps from Phase 1 parsed files."""
    if not config.get("maps", {}).get("enabled", False):
        return

    logger.info("\n%s", "=" * 60)
    logger.info("Extracting maps from source material...")
    logger.info("=" * 60)
    try:
        from src.extraction.maps import extract_maps

        extract_maps(
            _parsed_dir=output_root,
            output_dir=output_root,
            places_dir=output_root / "places",
            dates_dir=output_root / "dates",
            config=config,
        )
    except Exception as e:
        logger.error("  Error extracting maps: %s", e)


def _extract_external_maps(base_dir, grok_client, paths, config, logger):
    """Search for and import external maps."""
    if not config.get("external_maps", {}).get("enabled", False):
        return

    logger.info("\n%s", "=" * 60)
    logger.info("Searching for external maps...")
    logger.info("=" * 60)

    # Check OpenSERP availability
    openserp_available = False
    try:
        import requests

        logger.info("Checking OpenSERP availability (may take 30-60 seconds)...")
        response = requests.get(
            "http://localhost:7001/mega/search?text=test&limit=1", timeout=60
        )
        openserp_available = response.status_code == 200
        if openserp_available:
            logger.info("✓ OpenSERP detected on port 7001")
    except requests.ConnectionError:
        logger.warning("  OpenSERP not available - skipping external map search")
        logger.warning("  To enable: cd openserp && ./openserp serve -p 7001 &")
    except requests.Timeout:
        logger.warning("  OpenSERP timeout - skipping external map search")
    except Exception as e:
        logger.warning("  OpenSERP check failed: %s", e)

    if openserp_available:
        try:
            from src.extraction.openserp_maps import import_openserp_maps

            ext_config = config.get("external_maps", {})
            imported = import_openserp_maps(
                places_dir=paths["output_root"] / "places",
                output_dir=paths["output_root"] / "external_maps",
                grok_client=grok_client,
                max_places=ext_config.get("max_places"),
                search_limit=ext_config.get("search_limit", 50),
                openserp_url=ext_config.get("openserp_url", "http://localhost:7001"),
                page_timeout=ext_config.get("page_download_timeout", 10),
                image_timeout=ext_config.get("image_download_timeout", 30),
                image_storage_path=ext_config.get("image_storage_path"),
            )
            logger.info("  ✓ Imported %d maps via OpenSERP", imported)
        except Exception as e:
            logger.error("  Error with OpenSERP: %s", e)

    # Import from YAML if it exists
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
            logger.info("  ✓ Imported %d maps from YAML", yaml_imported)
        except Exception as e:
            logger.error("  Error importing from YAML: %s", e)


def _retry_missing_events(parsed_files, grok_client, logger):
    """Retry any parsed files that are missing event files."""
    missing_events = []
    for parsed_file in sorted(parsed_files):
        event_file = parsed_file.parent / parsed_file.name.replace(
            "-parsed.json", "-event.json"
        )
        if not event_file.exists():
            missing_events.append(parsed_file)

    if not missing_events:
        return 0, 0

    logger.warning("\n%d file(s) missing event files", len(missing_events))
    logger.info("Retrying with cache cleared...")

    retried = 0
    retry_failed = 0
    for parsed_file in missing_events:
        logger.info("Retrying: %s", parsed_file.name)

        # Clear only this chapter's cache entries (not the entire events cache)
        chapter_id = parsed_file.name.replace("-parsed.json", "")
        cache = grok_client._get_cache("events")
        for key in list(cache):
            val = cache.get(key, "")
            if chapter_id in str(val):
                cache.pop(key, None)

        try:
            output_file = extract_events(
                parsed_file=parsed_file,
                grok_client=grok_client,
                output_dir=parsed_file.parent,
            )
            if output_file:
                logger.info("  ✓ Generated: %s", output_file.name)
                retried += 1
            else:
                logger.warning("  ⏭ Skipped (footnotes only)")
        except Exception as e:
            logger.error("  ✗ Retry failed: %s", e)
            retry_failed += 1

    return retried, retry_failed


def _run_analysis(output_root, logger):
    """Run duplicate detection and group analysis."""
    # People duplicates
    logger.info("\n%s", "=" * 60)
    logger.info("Analyzing for duplicate people...")
    logger.info("=" * 60)

    people_dir = output_root / "people"
    if people_dir.exists():
        try:
            duplicate_report = people_dir / "duplicate_report.json"
            generate_duplicate_report(people_dir, duplicate_report)
            logger.info("  ✓ Saved: %s", duplicate_report.name)
        except Exception as e:
            logger.error("  ✗ Duplicate detection failed: %s", e)
    else:
        logger.warning("No people directory found")

    # Related groups
    logger.info("\n%s", "=" * 60)
    logger.info("Analyzing people groups for relationships...")
    logger.info("%s", "=" * 60)

    groups_dir = output_root / "people_groups"
    if groups_dir.exists():
        try:
            related_report = groups_dir / "related_groups_report.json"
            generate_related_groups_report(groups_dir, related_report)
            logger.info("  ✓ Saved: %s", related_report.name)
        except Exception as e:
            logger.error("  ✗ Related groups analysis failed: %s", e)
    else:
        logger.warning("No people groups directory found")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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

    # Step 0: Complete metadata
    logger.info("\n%s", "=" * 60)
    logger.info("Checking metadata completeness...")
    logger.info("=" * 60)
    _complete_metadata(base_dir, paths, logger)

    # Check for API key
    from dotenv import load_dotenv

    load_dotenv()

    if not os.getenv("GROK_API_KEY"):
        logger.error("GROK_API_KEY not found in environment")
        logger.error("Please create .env file with your API key")
        sys.exit(1)

    # Initialize Grok client
    cache_dir = paths["api_cache"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir)
    logger.info("Initialized Grok client with API cache: %s", cache_dir)

    # Ensure cache directories exist
    for cache_type in ["image_cache", "map_cache"]:
        if cache_type in paths:
            paths[cache_type].mkdir(parents=True, exist_ok=True)

    # Find parsed files
    output_root = paths["output_root"]
    parsed_files = list(output_root.rglob("*-parsed.json"))

    if not parsed_files:
        logger.error("No parsed files found in %s", output_root)
        logger.error("Please run phase1_parse.py first")
        sys.exit(1)

    logger.info("Found %d parsed file(s)", len(parsed_files))

    # -----------------------------------------------------------------------
    # Step 1: Parallel extraction of events + core entities
    #         (events, dates, places, people_groups, people)
    # -----------------------------------------------------------------------
    logger.info("\n%s", "=" * 60)
    logger.info("Processing all chapters in parallel...")
    logger.info("=" * 60)

    from src.extraction.batch_parallel import process_chapters_parallel

    max_parallel = config.get("concurrency", {}).get("max_parallel_chapters", 3)

    results = asyncio.run(
        process_chapters_parallel(
            parsed_files=parsed_files,
            grok_client=grok_client,
            output_root=output_root,
            config=config,
            max_parallel=max_parallel,
        )
    )

    processed = results["processed"]
    failed = results["failed"]

    logger.info("\n%s", "=" * 60)
    logger.info("Parallel processing complete!")
    logger.info("Processed: %d, Failed: %d", processed, failed)
    logger.info("%s", "=" * 60)

    # -----------------------------------------------------------------------
    # Step 2: Retry any missing event files
    # -----------------------------------------------------------------------
    logger.info("\n%s", "=" * 60)
    logger.info("Validating event file generation...")
    logger.info("=" * 60)

    retried, retry_failed = _retry_missing_events(parsed_files, grok_client, logger)
    processed += retried
    failed += retry_failed

    # -----------------------------------------------------------------------
    # Step 3: Optional entity extraction (weather, equipment, logistics,
    #         casualties, supplemental) — runs sequentially per event file
    # -----------------------------------------------------------------------
    any_optional = any(
        config.get(feature, {}).get("enabled", False)
        for feature in [
            "weather",
            "equipment",
            "logistics",
            "casualties",
            "supplemental_material",
        ]
    )

    if any_optional:
        logger.info("\n%s", "=" * 60)
        logger.info(
            "Extracting optional entities (weather, equipment, logistics, casualties, supplemental)..."
        )
        logger.info("=" * 60)

        event_files = sorted(output_root.rglob("*-event.json"))
        logger.info("Found %d event file(s)", len(event_files))

        for event_file in event_files:
            # Find corresponding parsed file
            parsed_file = event_file.parent / event_file.name.replace(
                "-event.json", "-parsed.json"
            )
            if not parsed_file.exists():
                parsed_file = None

            logger.info("  Processing: %s", event_file.name)
            _extract_optional_entities(
                event_file, parsed_file, grok_client, paths, config, logger
            )

    # -----------------------------------------------------------------------
    # Step 4: Maps extraction
    # -----------------------------------------------------------------------
    _extract_maps(output_root, config, logger)
    _extract_external_maps(base_dir, grok_client, paths, config, logger)

    # -----------------------------------------------------------------------
    # Step 5: Analysis
    # -----------------------------------------------------------------------
    _run_analysis(output_root, logger)

    # -----------------------------------------------------------------------
    # Done
    # -----------------------------------------------------------------------
    logger.info("\n%s", "=" * 60)
    logger.info("Phase 2 complete! Processed: %d, Failed: %d", processed, failed)
    logger.info("%s", "=" * 60)


if __name__ == "__main__":
    main()
