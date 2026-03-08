"""Concurrent extraction wrapper for phase2."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def extract_group1_concurrent(
    event_file: Path,
    grok_client: GrokClient,
    parsed_file: Path,
    paths: dict,
) -> None:
    """Extract Group 1: Dates, Places, Weather (parallel)."""
    from src.extraction.dates import extract_dates
    from src.extraction.places import extract_places
    from src.extraction.weather_central import extract_weather_central

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}

        # Dates
        futures["dates"] = executor.submit(  # type: ignore[call-arg]
            extract_dates,
            event_file=event_file,
            grok_client=grok_client,
            dates_dir=paths["output_root"] / "dates",
            parsed_file=parsed_file,
        )

        # Places
        futures["places"] = executor.submit(
            extract_places,
            event_file=event_file,
            grok_client=grok_client,
            places_dir=paths["output_root"] / "places",
            parsed_file=parsed_file,
        )

        # Weather
        futures["weather"] = executor.submit(
            extract_weather_central,
            event_file=event_file,
            grok_client=grok_client,
            weather_dir=paths["output_root"] / "weather",
            places_dir=paths["output_root"] / "places",
            dates_dir=paths["output_root"] / "dates",
            parsed_file=parsed_file,
            fetch_api=False,
            max_retries=3,
        )

        # Wait for all to complete
        for name, future in futures.items():
            try:
                result = future.result()
                if result:
                    logger.info("  ✓ %s extraction complete", name.capitalize())
            except Exception as e:
                logger.error("  ✗ %s extraction failed: %s", name.capitalize(), e)


def extract_group2_concurrent(
    event_file: Path,
    grok_client: GrokClient,
    paths: dict,
) -> None:
    """Extract Group 2: People, People Groups (parallel)."""
    from src.extraction.people import extract_people
    from src.extraction.people_groups import extract_people_groups

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}

        # People
        futures["people"] = executor.submit(  # type: ignore[call-arg]
            extract_people,
            event_file=event_file,
            grok_client=grok_client,
            people_dir=paths["output_root"] / "people",
        )

        # People Groups
        futures["groups"] = executor.submit(  # type: ignore[call-arg]
            extract_people_groups,
            event_file=event_file,
            grok_client=grok_client,
            groups_dir=paths["output_root"] / "people_groups",
        )

        # Wait for all to complete
        for name, future in futures.items():
            try:
                result = future.result()
                if result:
                    logger.info("  ✓ %s extraction complete", name.capitalize())
            except Exception as e:
                logger.error("  ✗ %s extraction failed: %s", name.capitalize(), e)


def extract_group3_sequential(
    event_file: Path,
    grok_client: GrokClient,
    paths: dict,
    config: dict,
) -> None:
    """Extract Group 3: Equipment (sequential, depends on Group 1 & 2)."""
    if not config.get("equipment", {}).get("enabled", False):
        return

    from src.extraction.equipment import extract_equipment_from_event

    try:
        equipment_dir = paths["output_root"] / "equipment"
        enable_enrichment = config.get("equipment", {}).get("enable_enrichment", False)
        verify_media = config.get("equipment", {}).get("verify_media_with_vision", True)

        equipment_files = extract_equipment_from_event(
            event_file=event_file,
            output_dir=equipment_dir,
            grok_client=grok_client,
            output_root=paths["output_root"],
            enable_enrichment=enable_enrichment,
            verify_media_with_vision=verify_media,
        )
        if equipment_files:
            logger.info(
                "  ✓ Equipment extraction complete (%d files)", len(equipment_files)
            )
    except Exception as e:
        logger.error("  ✗ Equipment extraction failed: %s", e)


def extract_group4_sequential(
    event_file: Path,
    grok_client: GrokClient,
    paths: dict,
    config: dict,
) -> None:
    """Extract Group 4: Logistics (sequential, depends on all previous)."""
    if not config.get("logistics", {}).get("enabled", False):
        return

    from src.extraction.logistics import extract_logistics_from_event

    try:
        logistics_dir = extract_logistics_from_event(
            event_file=event_file,
            output_root=paths["output_root"],
            grok_client=grok_client,
        )
        if logistics_dir:
            logger.info("  ✓ Logistics extraction complete")
    except Exception as e:
        logger.error("  ✗ Logistics extraction failed: %s", e)


def process_event_file_concurrent(
    event_file: Path,
    parsed_file: Path,
    grok_client: GrokClient,
    paths: dict,
    config: dict,
) -> bool:
    """Process single event file with concurrent extraction groups."""
    try:
        logger.info("Processing: %s", parsed_file.name)

        # Group 1: Dates, Places, Weather (parallel)
        extract_group1_concurrent(event_file, grok_client, parsed_file, paths)

        # Group 2: People, People Groups (parallel)
        extract_group2_concurrent(event_file, grok_client, paths)

        # Group 3: Equipment (sequential)
        extract_group3_sequential(event_file, grok_client, paths, config)

        # Group 4: Logistics (sequential)
        extract_group4_sequential(event_file, grok_client, paths, config)

        return True
    except Exception as e:
        logger.error("Failed to process %s: %s", parsed_file.name, e)
        return False


def process_files_concurrent(
    event_files: list,
    parsed_files: list,
    grok_client: GrokClient,
    paths: dict,
    config: dict,
    max_workers: int = 3,
) -> tuple[int, int]:
    """Process multiple event files concurrently."""
    processed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for event_file, parsed_file in zip(event_files, parsed_files):
            future = executor.submit(
                process_event_file_concurrent,
                event_file,
                parsed_file,
                grok_client,
                paths,
                config,
            )
            futures[future] = parsed_file.name

        for future in as_completed(futures):
            filename = futures[future]
            try:
                success = future.result()
                if success:
                    processed += 1
                    logger.info("✓ Completed: %s", filename)
                else:
                    failed += 1
                    logger.error("✗ Failed: %s", filename)
            except Exception as e:
                failed += 1
                logger.error("✗ Exception processing %s: %s", filename, e)

    return processed, failed
