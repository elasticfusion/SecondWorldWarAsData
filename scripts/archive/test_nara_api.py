#!/usr/bin/env python3
"""Test NARA API key and validate search cache behavior.

Usage:
    python3 scripts/test_nara_api.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import load_config
from src.utils.search_cache import cache_result, get_cached


def test_api_key():
    """Validate NARA API key with a simple search."""
    import requests

    config = load_config()
    api_key = config.get("api", {}).get("nara_api_key", "")
    if not api_key:
        print("❌ No nara_api_key in config.yaml")
        return False

    print(f"API key: {api_key[:8]}...{api_key[-4:]}")

    resp = requests.get(
        "https://catalog.archives.gov/proxy/records/search",
        params={"q": "SHAEF", "limit": 1},
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json()
        total = data.get("body", {}).get("hits", {}).get("total", {}).get("value", 0)
        print(f"✓ NARA API working — {total} results for 'SHAEF'")
        return True
    elif resp.status_code == 401:
        print(f"❌ Invalid API key (401)")
        return False
    elif resp.status_code == 429:
        print(f"❌ Rate limited (429) — monthly quota may be exhausted")
        return False
    else:
        print(f"❌ Unexpected status: {resp.status_code}")
        print(f"   Response: {resp.text[:200]}")
        return False


def test_positive_cache():
    """Test that positive results are cached."""
    source = "nara_test"
    query = "test_positive_query"
    result = "https://catalog.archives.gov/id/12345"

    # Clear any existing
    cache_result(source, query, result)
    cached = get_cached(source, query)

    if cached == result:
        print("✓ Positive cache: write and read works")
        return True
    else:
        print(f"❌ Positive cache failed: expected {result}, got {cached}")
        return False


def test_negative_cache():
    """Test that negative results (NOT_FOUND) are cached."""
    source = "nara_test"
    query = "test_negative_query"

    cache_result(source, query, None)
    cached = get_cached(source, query)

    if cached == "NOT_FOUND":
        print("✓ Negative cache: NOT_FOUND stored and retrieved")
        return True
    else:
        print(f"❌ Negative cache failed: expected NOT_FOUND, got {cached}")
        return False


def test_cache_miss():
    """Test that uncached queries return None."""
    cached = get_cached("nara_test", "never_searched_query_xyz")
    if cached is None:
        print("✓ Cache miss: returns None for unsearched queries")
        return True
    else:
        print(f"❌ Cache miss failed: expected None, got {cached}")
        return False


def test_nara_search_with_cache():
    """Test a real NARA search and verify caching."""
    import requests

    config = load_config()
    api_key = config.get("api", {}).get("nara_api_key", "")
    if not api_key:
        print("⚠ Skipping real search test (no API key)")
        return True

    query = "Eisenhower Bradley letter 1944"

    # Check cache first
    cached = get_cached("nara", query)
    if cached:
        print(f"✓ NARA search cached: {cached[:60]}...")
        return True

    # Real search
    resp = requests.get(
        "https://catalog.archives.gov/proxy/records/search",
        params={"q": query, "limit": 3},
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code == 200:
        hits = resp.json().get("body", {}).get("hits", {}).get("hits", [])
        if hits:
            title = hits[0].get("_source", {}).get("title", "No title")
            nara_id = hits[0].get("_source", {}).get("naId", "")
            url = f"https://catalog.archives.gov/id/{nara_id}" if nara_id else None
            cache_result("nara", query, url or title)
            print(f"✓ NARA search found: {title[:60]}")
            print(f"  Cached for future use")
        else:
            cache_result("nara", query, None)
            print("✓ NARA search: no results (negative cached)")
        return True
    else:
        print(f"❌ NARA search failed: {resp.status_code}")
        return False


def main():
    print("=== NARA API & Cache Tests ===\n")

    results = [
        test_positive_cache(),
        test_negative_cache(),
        test_cache_miss(),
        test_api_key(),
        test_nara_search_with_cache(),
    ]

    print(f"\n{'=' * 40}")
    passed = sum(results)
    print(f"Results: {passed}/{len(results)} passed")

    if all(results):
        print("\n✓ All tests passed. NARA integration ready.")
        print(f"  Monthly limit: 10,000 requests")
        print(f"  Cache TTL: 30 days (positive), 7 days (negative)")
        print(f"  Estimated usage per run: ~3,000 requests (for military records)")
    else:
        print("\n❌ Some tests failed. Check config and connectivity.")

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
