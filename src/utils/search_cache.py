"""Cache for external search results (Wikipedia, Grokipedia, OpenSERP, Archive.org, LOC).

Stores both positive and negative results in DynamoDB (AWS) or local diskcache (local).
Prevents redundant HTTP calls across runs. TTL-based expiry for re-checking.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default TTL: 30 days for positive, 7 days for negative
POSITIVE_TTL_DAYS = 30
NEGATIVE_TTL_DAYS = 7

_cache_backend = None


def _get_backend():
    """Get or create the cache backend."""
    global _cache_backend
    if _cache_backend is not None:
        return _cache_backend

    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})

    if aws.get("enabled"):
        _cache_backend = _DynamoBackend(
            table_name=aws.get("cache_table", "dev-wwii-api-cache"),
            region=aws.get("region", "us-east-1"),
        )
    else:
        _cache_backend = _LocalBackend(Path("cache/search_cache"))

    return _cache_backend


def _make_key(source: str, query: str) -> str:
    """Create cache key from source and query."""
    h = hashlib.sha256(f"{source}:{query}".encode()).hexdigest()[:16]
    return f"search#{source}#{h}"


def get_cached(source: str, query: str) -> Optional[str]:
    """Get cached search result. Returns content string, "NOT_FOUND" for negative cache, or None if not cached."""
    backend = _get_backend()
    key = _make_key(source, query)
    return backend.get(key)


def cache_result(source: str, query: str, result: Optional[str]) -> None:
    """Cache a search result. Pass None or empty string for negative cache."""
    backend = _get_backend()
    key = _make_key(source, query)
    if result:
        backend.put(key, result, ttl_days=POSITIVE_TTL_DAYS)
    else:
        backend.put(key, "NOT_FOUND", ttl_days=NEGATIVE_TTL_DAYS)


class _DynamoBackend:
    def __init__(self, table_name: str, region: str):
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, key: str) -> Optional[str]:
        try:
            resp = self._table.get_item(Key={"cache_key": key})
            item = resp.get("Item")
            if not item:
                return None
            # Check TTL
            ttl = item.get("ttl", 0)
            if ttl and int(time.time()) > ttl:
                return None  # Expired
            return item.get("response", "")
        except Exception:
            return None

    def put(self, key: str, value: str, ttl_days: int = 30) -> None:
        try:
            self._table.put_item(
                Item={
                    "cache_key": key,
                    "response": value,
                    "ttl": int(time.time()) + (ttl_days * 86400),
                }
            )
        except Exception as e:
            logger.debug("Cache write failed: %s", e)


class _LocalBackend:
    def __init__(self, cache_dir: Path):
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[str]:
        f = self._dir / f"{key}.json"
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text())
            if data.get("ttl", 0) and int(time.time()) > data["ttl"]:
                f.unlink()
                return None
            return data.get("response", "")
        except Exception:
            return None

    def put(self, key: str, value: str, ttl_days: int = 30) -> None:
        f = self._dir / f"{key}.json"
        f.write_text(
            json.dumps(
                {"response": value, "ttl": int(time.time()) + (ttl_days * 86400)}
            )
        )
