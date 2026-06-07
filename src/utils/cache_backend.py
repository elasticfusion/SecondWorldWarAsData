"""Cache backend abstraction for diskcache and DynamoDB."""

import logging
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

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
        from diskcache import Cache  # type: ignore[import-untyped]

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
        self._local: dict = {}  # Preloaded cache entries
        self._preloaded = False

    def preload(self) -> int:
        """Scan all entries with this prefix into memory. Returns count loaded."""
        self._local = {}
        kwargs: dict = {
            "ProjectionExpression": "cache_key, #r",
            "ExpressionAttributeNames": {"#r": "response"},
        }
        if self.prefix:
            kwargs["FilterExpression"] = "begins_with(cache_key, :prefix)"
            kwargs["ExpressionAttributeValues"] = {":prefix": self.prefix}
        while True:
            resp = self._table.scan(**kwargs)
            for item in resp.get("Items", []):
                pk = item.get("cache_key", "")
                self._local[pk] = item.get("response", "")
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        self._preloaded = True
        return len(self._local)

    def _pk(self, key: str) -> str:
        return f"{self.prefix}#{key}" if self.prefix else key

    def __getitem__(self, key: str) -> str:
        pk = self._pk(key)
        if self._preloaded and pk in self._local:
            return self._local[pk]
        resp = self._table.get_item(Key={"cache_key": pk})
        item = resp.get("Item")
        if not item:
            raise KeyError(key)
        value = item["response"]
        self._local[pk] = value  # Cache for future reads
        return value

    def __setitem__(self, key: str, value: str) -> None:
        pk = self._pk(key)
        self._table.put_item(
            Item={
                "cache_key": pk,
                "response": value,
                "created_at": int(time.time()),
                "ttl": int(time.time()) + (self.ttl_days * 86400),
            }
        )
        self._local[pk] = value

    def __contains__(self, key: str) -> bool:
        pk = self._pk(key)
        if self._preloaded:
            return pk in self._local
        # Fetch full item so __getitem__ won't need a second read
        resp = self._table.get_item(Key={"cache_key": pk})
        item = resp.get("Item")
        if item and "response" in item:
            self._local[pk] = item["response"]
            return True
        return False

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
        with self._table.batch_writer() as batch:
            while True:
                resp = self._table.scan(**scan_kwargs)
                for item in resp.get("Items", []):
                    batch.delete_item(Key={"cache_key": item["cache_key"]})
                if "LastEvaluatedKey" not in resp:
                    break
                scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def get_sub_cache(self, name: str) -> "DynamoCacheBackend":
        new_prefix = f"{self.prefix}/{name}" if self.prefix else name
        backend = DynamoCacheBackend.__new__(DynamoCacheBackend)
        backend.table_name = self.table_name
        backend.prefix = new_prefix
        backend.ttl_days = self.ttl_days
        backend._dynamo = self._dynamo
        backend._table = self._table
        backend._local = self._local  # Share parent's preloaded data
        backend._preloaded = self._preloaded
        return backend
