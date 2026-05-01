"""Cache backend abstraction for diskcache and DynamoDB."""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class CacheBackend(Protocol):
    """Protocol for API response caching.

    Supports dict-style access: backend[key], backend[key] = value, key in backend.
    Also supports sub-caches via get_sub_cache(name) for per-type/per-book routing.
    """

    def __getitem__(self, key: str) -> str: ...
    def __setitem__(self, key: str, value: str) -> None: ...
    def __contains__(self, key: str) -> bool: ...
    def pop(self, key: str, default: Any = None) -> Any: ...
    def clear(self) -> None: ...
    def get_sub_cache(self, name: str) -> "CacheBackend": ...


class DiskCacheBackend:
    """diskcache-backed cache (current default). Wraps diskcache.Cache with the CacheBackend interface."""

    def __init__(self, cache_dir: Path):
        from diskcache import Cache

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache = Cache(str(self.cache_dir))

    def __getitem__(self, key: str) -> str:
        return self._cache[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._cache[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def pop(self, key: str, default: Any = None) -> Any:
        return self._cache.pop(key, default)

    def clear(self) -> None:
        self._cache.clear()

    def get_sub_cache(self, name: str) -> "DiskCacheBackend":
        return DiskCacheBackend(self.cache_dir / name)


class DynamoCacheBackend:
    """DynamoDB-backed cache for serverless deployments."""

    def __init__(
        self,
        table_name: str,
        prefix: str = "",
        region: str = "us-east-1",
        ttl_days: int = 90,
    ):
        import boto3

        self.table_name = table_name
        self.prefix = prefix
        self.ttl_days = ttl_days
        self._dynamo = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamo.Table(table_name)

    def _pk(self, key: str) -> str:
        return f"{self.prefix}#{key}" if self.prefix else key

    def __getitem__(self, key: str) -> str:
        resp = self._table.get_item(Key={"cache_key": self._pk(key)})
        item = resp.get("Item")
        if not item:
            raise KeyError(key)
        return item["response"]

    def __setitem__(self, key: str, value: str) -> None:
        self._table.put_item(
            Item={
                "cache_key": self._pk(key),
                "response": value,
                "created_at": int(time.time()),
                "ttl": int(time.time()) + (self.ttl_days * 86400),
            }
        )

    def __contains__(self, key: str) -> bool:
        resp = self._table.get_item(
            Key={"cache_key": self._pk(key)}, ProjectionExpression="cache_key"
        )
        return "Item" in resp

    def pop(self, key: str, default: Any = None) -> Any:
        try:
            value = self[key]
            self._table.delete_item(Key={"cache_key": self._pk(key)})
            return value
        except KeyError:
            return default

    def clear(self) -> None:
        """Delete all items with this prefix. Use with caution."""
        scan_kwargs: dict = {}  # type: ignore[type-arg]
        if self.prefix:
            scan_kwargs["FilterExpression"] = "begins_with(cache_key, :prefix)"
            scan_kwargs["ExpressionAttributeValues"] = {":prefix": self.prefix}
        resp = self._table.scan(**scan_kwargs)
        with self._table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"cache_key": item["cache_key"]})

    def get_sub_cache(self, name: str) -> "DynamoCacheBackend":
        new_prefix = f"{self.prefix}/{name}" if self.prefix else name
        backend = DynamoCacheBackend.__new__(DynamoCacheBackend)
        backend.table_name = self.table_name
        backend.prefix = new_prefix
        backend.ttl_days = self.ttl_days
        backend._dynamo = self._dynamo
        backend._table = self._table
        return backend
