"""S3-backed lazy file accessor for ECS pipeline.

Provides a local directory that lazily downloads files from S3 on first access.
Files are cached locally after download. Directory listings (glob) use S3 list
operations without downloading file contents.

Usage:
    accessor = S3LazyAccessor(bucket, "output/people/", local_dir)
    accessor.ensure_ready()  # downloads index.json only

    # Code uses local_dir as normal — files download transparently on read
    for f in accessor.local_dir.glob("*.json"):
        data = json.loads(f.read_text())  # triggers S3 download if not cached
"""

import logging
from pathlib import Path
from typing import Optional, Set

import boto3

logger = logging.getLogger(__name__)


class S3LazyAccessor:
    """Lazy S3 file accessor with local caching."""

    def __init__(
        self, bucket: str, prefix: str, local_dir: Path, region: str = "us-east-1"
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.local_dir = local_dir
        self._s3 = boto3.client("s3", region_name=region)
        self._keys: Optional[Set[str]] = None
        self._downloaded: Set[str] = set()

    def ensure_ready(self, eager_files: Optional[list] = None):
        """Prepare the accessor. Downloads only specified files eagerly (e.g., index.json)."""
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self._list_keys()
        # Create empty placeholder files so glob() finds them
        assert self._keys is not None
        for key in self._keys:
            filename = key[len(self.prefix) :]
            local = self.local_dir / filename
            if not local.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                local.touch()
        # Eagerly download specific files
        for name in eager_files or []:
            self._download(name)

    def _list_keys(self):
        """List all S3 keys under prefix."""
        if self._keys is not None:
            return
        self._keys = set()
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                self._keys.add(obj["Key"])
        logger.info("S3LazyAccessor: %d files under %s", len(self._keys), self.prefix)

    def _download(self, filename: str) -> Path:
        """Download a single file from S3 to local cache."""
        key = self.prefix + filename
        local = self.local_dir / filename
        if filename in self._downloaded:
            return local
        if local.exists() and local.stat().st_size > 0:
            self._downloaded.add(filename)
            return local
        local.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, key, str(local))
        self._downloaded.add(filename)
        return local

    def get(self, filename: str) -> Path:
        """Get local path for a file, downloading if needed."""
        return self._download(filename)
