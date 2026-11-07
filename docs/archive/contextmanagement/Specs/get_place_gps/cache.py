"""Cache management for GPS data."""

from pathlib import Path
import json
import logging
import re
import unicodedata
import hashlib

logger = logging.getLogger(__name__)

def _sanitize_key(key: str) -> str:
    """Sanitize cache key to prevent path traversal and collisions."""
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', key)
    key_hash = hashlib.md5(key.encode()).hexdigest()[:8]
    return f"{sanitized}_{key_hash}"

def _normalize_place_name(name: str) -> str:
    """Normalize place name by removing accents and converting to lowercase."""
    nfd = unicodedata.normalize('NFD', name)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn').lower().strip()

def get_cached_result(key: str, cache_dir: Path | None) -> dict | None:
    """Retrieve cached GPS data from file if it exists."""
    if not cache_dir:
        return None

    safe_key = _sanitize_key(key)
    file_path = cache_dir / f"{safe_key}.json"

    if not file_path.is_file():
        return None

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("Cache hit for key '%s' from %s", key, file_path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Invalid cache file for '%s': %s", key, exc)
        return None

def store_cached_result(key: str, data: dict, cache_dir: Path | None) -> None:
    """Save GPS data to a JSON file in the cache directory."""
    if not cache_dir:
        logger.debug("No cache directory – skipping write for '%s'", key)
        return

    safe_key = _sanitize_key(key)
    file_path = cache_dir / f"{safe_key}.json"

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Wrote cache entry for '%s' to %s", key, file_path)
    except (OSError, TypeError) as exc:
        logger.error("Failed to write cache for '%s' to %s: %s", key, file_path, exc)

def extract_place_from_response(place_name: str,
                                response: list | dict) -> dict | None:
    """Extract specific place data from API response - only exact matches."""
    place_normalized = _normalize_place_name(place_name)

    if isinstance(response, dict):
        # Check if it's a single place object with "place" key
        if "place" in response:
            if _normalize_place_name(response.get("place", "")) == place_normalized:
                return response
            return None

        # Check dict values for matching place
        for value in response.values():
            if isinstance(value, dict):
                if _normalize_place_name(value.get("place", "")) == place_normalized:
                    return value
        return None

    if not isinstance(response, list) or not response:
        return None

    # Search list for matching place
    for item in response:
        if isinstance(item, dict):
            if _normalize_place_name(item.get("place", "")) == place_normalized:
                return item

    logger.warning("Place '%s' not found in API response", place_name)
    return None
