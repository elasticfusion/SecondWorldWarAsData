"""
Combined map search: Grok whitelist first, then OpenSERP.

Runs high-quality whitelisted sources first, then broader search.
Duplicate detection prevents re-importing same maps.
"""

import logging
from pathlib import Path
from typing import Optional

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def import_all_external_maps(
    places_dir: Path,
    output_dir: Path,
    image_storage_path: Path,
    grok_client: GrokClient,
    max_places: Optional[int] = None,
    use_openserp: bool = True,
    openserp_url: str = "http://localhost:7001",
    skip_searched: bool = True,
) -> tuple[int, int]:
    """Import maps using both Grok whitelist and OpenSERP.

    Strategy:
    1. Grok searches whitelisted sites (high quality)
    2. OpenSERP searches broader web (if enabled)
    3. Duplicate detection prevents re-imports
    4. Search history prevents re-searching

    Args:
        places_dir: Directory containing place JSON files
        output_dir: Directory to save imported maps
        image_storage_path: Path to store downloaded images
        grok_client: Grok API client
        max_places: Maximum places to search (None for all)
        use_openserp: Whether to run OpenSERP after Grok
        openserp_url: OpenSERP service URL
        skip_searched: Skip places previously searched by OpenSERP

    Returns: (grok_imported, openserp_imported)
    """
    logger.info("=" * 60)
    logger.info("External Maps Import - Combined Strategy")
    logger.info("=" * 60)

    # Phase 1: Grok whitelist search (high quality)
    logger.info("\n📍 Phase 1: Grok Whitelist Search")
    logger.info("Using whitelisted sites from domain_blacklist.yaml")
    logger.info("-" * 60)

    from src.extraction.grok_search_maps import import_grok_search_maps

    blacklist_file = Path("config/domain_blacklist.yaml")

    grok_imported = import_grok_search_maps(
        places_dir=places_dir,
        output_dir=output_dir,
        image_storage_path=image_storage_path,
        grok_client=grok_client,
        max_places=max_places,
        blacklist_file=blacklist_file,
    )

    logger.info(f"\n✅ Phase 1 complete: {grok_imported} maps from whitelisted sites")

    # Phase 2: OpenSERP (broader search)
    openserp_imported = 0

    if use_openserp:
        logger.info("\n📍 Phase 2: OpenSERP Search")
        logger.info("Searching broader web (Google, Bing, DuckDuckGo)")
        logger.info("Duplicate detection will skip maps already found")
        logger.info("-" * 60)

        try:
            from src.extraction.openserp_maps import import_openserp_maps

            openserp_imported = import_openserp_maps(
                places_dir=places_dir,
                output_dir=output_dir,
                grok_client=grok_client,
                max_places=max_places,
                openserp_url=openserp_url,
                image_storage_path=str(image_storage_path),
                skip_searched=skip_searched,
            )

            logger.info(
                f"\n✅ Phase 2 complete: {openserp_imported} additional maps from OpenSERP"
            )

        except Exception as e:
            logger.warning(f"\n⚠️  OpenSERP search failed: {e}")
            logger.info("Continuing with Grok results only")

    # Summary
    total = grok_imported + openserp_imported
    logger.info("\n" + "=" * 60)
    logger.info("Import Summary")
    logger.info("=" * 60)
    logger.info(f"Grok whitelist:  {grok_imported} maps")
    logger.info(f"OpenSERP:        {openserp_imported} maps")
    logger.info(f"Total imported:  {total} maps")
    logger.info("=" * 60)

    return grok_imported, openserp_imported


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Import external maps using combined strategy"
    )
    parser.add_argument(
        "--max-places",
        type=int,
        default=5,
        help="Maximum places to search (default: 5 for testing)",
    )
    parser.add_argument(
        "--skip-openserp",
        action="store_true",
        help="Skip OpenSERP search (Grok whitelist only)",
    )
    parser.add_argument(
        "--no-skip-searched",
        action="store_true",
        help="Re-search places that were previously searched by OpenSERP",
    )
    parser.add_argument(
        "--openserp-url",
        default="http://localhost:7001",
        help="OpenSERP service URL",
    )
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent.parent
    places_dir = project_root / "output" / "places"
    output_dir = project_root / "output" / "external_maps"
    image_storage_path = project_root / "filestore" / "external_maps"
    cache_dir = project_root / "cache" / "api"

    grok_client = GrokClient(cache_dir)

    import_all_external_maps(
        places_dir=places_dir,
        output_dir=output_dir,
        image_storage_path=image_storage_path,
        grok_client=grok_client,
        max_places=args.max_places,
        use_openserp=not args.skip_openserp,
        openserp_url=args.openserp_url,
        skip_searched=not args.no_skip_searched,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
