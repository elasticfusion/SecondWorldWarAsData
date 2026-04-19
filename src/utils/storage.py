"""Storage abstraction layer for local filesystem and S3."""

import json
import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Storage(Protocol):
    """Protocol for reading/writing JSON and files."""

    def read_json(self, path: str) -> dict[str, Any]: ...
    def write_json(self, path: str, data: dict[str, Any]) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list_files(self, prefix: str, pattern: str = "*.json") -> list[str]: ...
    def delete(self, path: str) -> None: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...


class LocalStorage:
    """Filesystem-backed storage."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def _resolve(self, path: str) -> Path:
        return self.base_dir / path

    def read_json(self, path: str) -> dict[str, Any]:
        return json.loads(self._resolve(path).read_text(encoding="utf-8"))

    def write_json(self, path: str, data: dict[str, Any]) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def list_files(self, prefix: str, pattern: str = "*.json") -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        return [
            str(f.relative_to(self.base_dir))
            for f in sorted(base.glob(pattern))
            if f.is_file()
        ]

    def delete(self, path: str) -> None:
        p = self._resolve(path)
        if p.exists():
            p.unlink()

    def read_bytes(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class S3Storage:
    """S3-backed storage with same interface as LocalStorage."""

    def __init__(self, bucket: str, prefix: str = "", region: str = "us-east-1"):
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.s3 = boto3.client("s3", region_name=region)

    def _key(self, path: str) -> str:
        return f"{self.prefix}/{path}" if self.prefix else path

    def read_json(self, path: str) -> dict[str, Any]:
        resp = self.s3.get_object(Bucket=self.bucket, Key=self._key(path))
        return json.loads(resp["Body"].read().decode("utf-8"))

    def write_json(self, path: str, data: dict[str, Any]) -> None:
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=self._key(path), Body=body)

    def exists(self, path: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(path))
            return True
        except self.s3.exceptions.ClientError:
            return False

    def list_files(self, prefix: str, pattern: str = "*.json") -> list[str]:
        full_prefix = self._key(prefix)
        if not full_prefix.endswith("/"):
            full_prefix += "/"
        results = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Strip the storage prefix to return relative paths
                rel = key[len(self.prefix) + 1 :] if self.prefix else key
                if fnmatch(rel.split("/")[-1], pattern):
                    results.append(rel)
        return sorted(results)

    def delete(self, path: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._key(path))

    def read_bytes(self, path: str) -> bytes:
        resp = self.s3.get_object(Bucket=self.bucket, Key=self._key(path))
        return resp["Body"].read()

    def write_bytes(self, path: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(path), Body=data)
