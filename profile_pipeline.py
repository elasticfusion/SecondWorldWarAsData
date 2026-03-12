#!/usr/bin/env python3
"""Profile single chapter extraction to identify bottlenecks."""

import cProfile
import pstats
from pathlib import Path

from dotenv import load_dotenv

from src.utils.config import load_config, get_paths
from src.grok_client import GrokClient
from src.extraction.events import extract_events
from src.extraction.dates import extract_dates
from src.extraction.places import extract_places
from src.extraction.people import extract_people
from src.extraction.people_groups import extract_people_groups

load_dotenv()


def profile_single_chapter():
    """Profile extraction for one chapter."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    # Find first parsed file
    parsed_files = list(paths["output_root"].rglob("*-parsed.json"))
    if not parsed_files:
        print("No parsed files found")
        return

    parsed_file = parsed_files[0]
    print(f"Profiling: {parsed_file.name}\n")

    # Initialize Grok client
    grok_client = GrokClient(paths["api_cache"])

    # Profile event extraction
    profiler = cProfile.Profile()
    profiler.enable()

    event_file = extract_events(parsed_file, grok_client, parsed_file.parent)
    if event_file:
        extract_dates(
            event_file, grok_client, paths["output_root"] / "dates", parsed_file
        )
        extract_places(
            event_file, grok_client, paths["output_root"] / "places", parsed_file
        )
        extract_people(event_file, grok_client, paths["output_root"])
        extract_people_groups(event_file, grok_client, paths["output_root"])

    profiler.disable()

    # Save and print stats
    stats_file = base_dir / "logs" / "profile_stats.prof"
    profiler.dump_stats(str(stats_file))

    ps = pstats.Stats(profiler).sort_stats("cumulative")
    ps.print_stats(30)

    print(f"\n✓ Saved to: {stats_file}")


if __name__ == "__main__":
    profile_single_chapter()
