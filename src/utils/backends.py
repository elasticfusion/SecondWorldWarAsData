"""Factory functions for Storage and CacheBackend based on config."""

from pathlib import Path

from src.utils.cache_backend import CacheBackend, DiskCacheBackend
from src.utils.storage import LocalStorage, Storage


def create_storage(config: dict, base_dir: Path) -> Storage:
    """Create Storage backend based on config.

    Returns S3Storage if aws.enabled is true, otherwise LocalStorage.
    """
    aws = config.get("aws", {})
    if aws.get("enabled"):
        from src.utils.storage import S3Storage

        return S3Storage(
            bucket=aws["s3_bucket"],
            prefix=aws.get("s3_prefix", ""),
            region=aws.get("region", "us-east-1"),
        )
    return LocalStorage(base_dir)


def create_cache_backend(config: dict, cache_dir: Path) -> CacheBackend:
    """Create CacheBackend based on config.

    Returns DynamoCacheBackend if aws.enabled is true, otherwise DiskCacheBackend.
    """
    aws = config.get("aws", {})
    if aws.get("enabled"):
        from src.utils.cache_backend import DynamoCacheBackend

        return DynamoCacheBackend(
            table_name=aws.get("cache_table", "wwii-api-cache"),
            region=aws.get("region", "us-east-1"),
            ttl_days=aws.get("cache_ttl_days", 90),
        )
    return DiskCacheBackend(cache_dir)
