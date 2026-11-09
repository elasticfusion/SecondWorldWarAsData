"""A small in-memory S3 client for unit tests: the calls the OCR merge / promote paths make, with
botocore's error shapes (NoSuchKey / 404 / PreconditionFailed) so code under test sees what it
would see from S3."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from botocore.exceptions import ClientError


def _error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, *_args) -> bytes:
        return self._data


class _Paginator:
    def __init__(self, s3: "FakeS3"):
        self._s3 = s3

    def paginate(self, Bucket: str, Prefix: str = ""):  # noqa: N803 - boto3 casing
        keys = sorted(
            k for (b, k) in self._s3.objects if b == Bucket and k.startswith(Prefix)
        )
        return [{"Contents": [{"Key": k} for k in keys]}] if keys else [{}]


class FakeS3:
    def __init__(self, objects: Dict[str, bytes] = None, bucket: str = "buck"):
        self.objects: Dict[tuple, bytes] = {
            (bucket, k): (v if isinstance(v, bytes) else v.encode("utf-8"))
            for k, v in (objects or {}).items()
        }
        self.modified: Dict[tuple, datetime] = {
            k: datetime.now(timezone.utc) for k in self.objects
        }
        self.puts: List[str] = []  # keys in write order
        self.deletes: List[str] = []

    def text(self, key: str, bucket: str = "buck") -> str:
        return self.objects[(bucket, key)].decode("utf-8")

    def keys(self, prefix: str = "", bucket: str = "buck") -> List[str]:
        return sorted(
            k for (b, k) in self.objects if b == bucket and k.startswith(prefix)
        )

    def _etag(self, bucket: str, key: str) -> str:
        return (
            '"' + hashlib.md5(self.objects[(bucket, key)]).hexdigest() + '"'
        )  # noqa: S324

    def get_object(self, Bucket: str, Key: str, **_kw):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise _error("NoSuchKey", "GetObject")
        return {
            "Body": _Body(self.objects[(Bucket, Key)]),
            "ETag": self._etag(Bucket, Key),
        }

    def head_object(self, Bucket: str, Key: str):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise _error("404", "HeadObject")
        return {
            "ContentLength": len(self.objects[(Bucket, Key)]),
            "Metadata": {},
            "LastModified": self.modified[(Bucket, Key)],
            "ETag": self._etag(Bucket, Key),
        }

    def put_object(  # noqa: N803
        self, Bucket, Key, Body=b"", IfNoneMatch=None, IfMatch=None, **_kw
    ):
        exists = (Bucket, Key) in self.objects
        if IfNoneMatch == "*" and exists:
            raise _error("PreconditionFailed", "PutObject")
        if IfMatch is not None and (not exists or self._etag(Bucket, Key) != IfMatch):
            raise _error("PreconditionFailed", "PutObject")
        self.objects[(Bucket, Key)] = (
            Body if isinstance(Body, bytes) else Body.encode("utf-8")
        )
        self.modified[(Bucket, Key)] = datetime.now(timezone.utc)
        self.puts.append(Key)
        return {}

    def age(self, key: str, seconds: float, bucket: str = "buck") -> None:
        """Make ``key`` look ``seconds`` older (as if written earlier)."""
        was = self.modified[(bucket, key)].timestamp()
        self.modified[(bucket, key)] = datetime.fromtimestamp(
            was - seconds, timezone.utc
        )

    def delete_object(self, Bucket: str, Key: str, IfMatch=None):  # noqa: N803
        if IfMatch is not None and (
            (Bucket, Key) not in self.objects or self._etag(Bucket, Key) != IfMatch
        ):
            raise _error("PreconditionFailed", "DeleteObject")
        self.objects.pop((Bucket, Key), None)
        self.modified.pop((Bucket, Key), None)
        self.deletes.append(Key)
        return {}

    def get_paginator(self, _name: str) -> _Paginator:
        return _Paginator(self)
