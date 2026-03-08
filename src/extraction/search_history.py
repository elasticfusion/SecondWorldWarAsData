"""
OpenSERP search history tracking.

Tracks which places have been searched to avoid duplicate searches.
"""

import json
from pathlib import Path
from typing import Set
from datetime import datetime


class SearchHistory:
    """Track OpenSERP search history."""

    def __init__(self, history_file: Path = Path("cache/openserp_search_history.json")):
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.searched_places: Set[str] = self._load()

    def _load(self) -> Set[str]:
        """Load search history from file."""
        if not self.history_file.exists():
            return set()

        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("searched_places", []))
        except Exception:  # pylint: disable=broad-exception-caught
            return set()

    def _save(self):
        """Save search history to file."""
        data = {
            "searched_places": sorted(list(self.searched_places)),
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "total_searches": len(self.searched_places),
        }
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def has_searched(self, place_name: str) -> bool:
        """Check if place has been searched."""
        return place_name.lower() in self.searched_places

    def mark_searched(self, place_name: str):
        """Mark place as searched."""
        self.searched_places.add(place_name.lower())
        self._save()

    def get_downloaded_urls(self, output_dir: Path) -> Set[str]:
        """Get all URLs from previously downloaded maps."""
        urls: Set[str] = set()

        if not output_dir.exists():
            return urls

        for json_file in output_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                # Check both URL fields
                if url := data.get("external_source_url"):
                    urls.add(url)
                if url := data.get("source_url"):
                    urls.add(url)

            except Exception:  # pylint: disable=broad-exception-caught
                continue

        return urls

    def clear(self):
        """Clear search history."""
        self.searched_places.clear()
        if self.history_file.exists():
            self.history_file.unlink()
