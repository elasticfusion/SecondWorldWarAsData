"""
OpenSERP search history tracking.

Tracks which places have been searched to avoid duplicate searches.
"""

import json
from pathlib import Path
from typing import Set
from datetime import datetime

SHARED_TTL_DAYS = 90  # the OpenSERP re-search window


class SearchHistory:
    """Track OpenSERP search history: the local file plus the shared search cache (DynamoDB in
    AWS), so the history survives the task (a Spot reclaim, the next run) - task 1.2f.
    """

    def __init__(self, history_file: Path = Path("cache/openserp_search_history.json")):
        """Initialize search history tracker with optional file path."""
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

    @staticmethod
    def _shared_key(name: str) -> str:
        from src.utils.search_cache import _make_key

        return _make_key("openserp_maps_searched", name)

    def has_searched(self, place_name: str) -> bool:
        """Check if place has been searched (here, or by an earlier task)."""
        name = place_name.lower()
        if name in self.searched_places:
            return True
        try:
            from src.utils.search_cache import _get_backend

            if _get_backend().get(self._shared_key(name)):
                self.searched_places.add(name)
                return True
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # unknown: search (the cache counts its own outage)
        return False

    def mark_searched(self, place_name: str):
        """Mark place as searched (local file + shared cache)."""
        name = place_name.lower()
        self.searched_places.add(name)
        self._save()
        try:
            from src.utils.search_cache import _get_backend

            _get_backend().put(self._shared_key(name), "1", ttl_days=SHARED_TTL_DAYS)
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # kept locally; another task may search it again

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
