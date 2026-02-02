# cache_handler.py
"""
Submodule for permanent caching of place queries across events and chapters.

This module uses a JSON file for persistent storage of API responses for places,
keyed by a unique identifier (e.g., place name + context hash). It can be called
from groksubmit.py to check for cached results before making API calls and to
store new responses.
"""

import json
import os
import hashlib

CACHE_FILE = "place_cache.json"  # Persistent cache file in the current working directory; adjust path if needed


def load_cache():
    """Load the cache from the JSON file if it exists."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading cache: {e}. Initializing empty cache.")
            return {}
    return {}


def save_cache(cache):
    """Save the cache to the JSON file."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=4)
    except IOError as e:
        print(f"Error saving cache: {e}")


def get_cached_response(place, summary):
    """Retrieve a cached response for the given place and context."""
    cache = load_cache()
    cache_key = generate_cache_key(place, summary)
    return cache.get(cache_key)


def cache_response(place, summary, response_obj):
    """Store a response in the cache for the given place and context."""
    cache = load_cache()
    cache_key = generate_cache_key(place, summary)
    cache[cache_key] = response_obj
    save_cache(cache)


def generate_cache_key(place, summary):
    """Generate a unique cache key based on place and summary context."""
    # Use MD5 hash for summary to handle long strings
    summary_hash = hashlib.md5(summary.encode('utf-8')).hexdigest()
    return f"{place}_{summary_hash}"