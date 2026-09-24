"""Tests for the OCR markdown-review Lambda handler.

Uses a tiny in-memory fake S3 client (only the methods the handler calls) so no
AWS or moto is required.
"""

from __future__ import annotations

import base64
import json

import pytest

from lambda_handlers import mdreview_ui_handler as h


class FakeS3:
    """Minimal in-memory S3 stand-in for the handler's access patterns."""

    def __init__(self, objects: dict):
        # objects: {key: bytes|str}
        self.store = {
            k: (v.encode() if isinstance(v, str) else v) for k, v in objects.items()
        }
        self.deleted: list = []

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.store:
            raise RuntimeError("NoSuchKey")
        data = self.store[Key]
        return {"Body": _Body(data)}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.store:
            raise RuntimeError("404")
        return {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.store[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_paginator(self, _name):
        store = self.store

        class _Pag:
            def paginate(self, Bucket, Prefix):  # noqa: N803
                keys = [k for k in store if k.startswith(Prefix)]
                yield {"Contents": [{"Key": k} for k in keys]}

        return _Pag()


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


_RECOVERY_NO_TABLE = json.dumps(
    {
        "recovered_a_table": False,
        "flattened_hints": [
            {
                "snapshots": [
                    {
                        "label": "170300",
                        "descriptor": "March South",
                        "groups": [
                            {"group": "CC-A", "units": ["40", "48", "A/33"]},
                            {"group": "CC-B", "units": ["31", "23"]},
                        ],
                    }
                ]
            }
        ],
    }
)
_RECOVERY_WITH_TABLE = json.dumps({"recovered_a_table": True})


def _event(method, path, params=None, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": params,
        "body": body,
    }


def _s3_with_two_pages():
    return FakeS3(
        {
            # p156 flagged (no table), p155 recovered (has table)
            "ocr-output/bookX/recovery/p156.recovery.json": _RECOVERY_NO_TABLE,
            "ocr-output/bookX/recovery/p156.png": b"\x89PNG-fake",
            "ocr-output/bookX/recovery/p155.recovery.json": _RECOVERY_WITH_TABLE,
        }
    )


def test_list_flagged_only_includes_unrecovered(monkeypatch):
    s3 = _s3_with_two_pages()
    pages = h._list_flagged_pages(s3, "b")
    assert pages == [{"book": "bookX", "page": 156, "has_review": False}]


def test_list_flagged_marks_has_review():
    s3 = _s3_with_two_pages()
    s3.store["ocr-output/bookX/reviewed/p156.md"] = b"edited"
    pages = h._list_flagged_pages(s3, "b")
    assert pages[0]["has_review"] is True


def test_parse_recovery_key():
    assert h._parse_recovery_key("ocr-output/bk/recovery/p42.recovery.json") == (
        "bk",
        42,
    )
    assert h._parse_recovery_key("ocr-output/bk/chunk-p1/input/input.md") == (
        None,
        None,
    )


def test_scaffold_from_hints_renders_groups():
    doc = json.loads(_RECOVERY_NO_TABLE)
    md = h._scaffold_from_hints(doc["flattened_hints"])
    assert "### 170300 — March South" in md
    assert "**CC-A**: 40, 48, A/33" in md


def test_page_snippet_prefers_reviewed():
    s3 = _s3_with_two_pages()
    s3.store["ocr-output/bookX/reviewed/p156.md"] = b"HUMAN EDIT"
    assert h._page_snippet(s3, "b", "bookX", 156) == "HUMAN EDIT"


def test_page_snippet_falls_back_to_scaffold():
    s3 = _s3_with_two_pages()
    snip = h._page_snippet(s3, "b", "bookX", 156)
    assert "CC-A" in snip and "Reviewer:" in snip


def test_get_page_returns_image_and_snippet(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "b")
    s3 = _s3_with_two_pages()
    resp = h._get_page(
        _event("GET", "/mdreview/api/page", {"book": "bookX", "page": "156"}), s3, "b"
    )
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["page"] == 156
    assert base64.b64decode(body["image_b64"]) == b"\x89PNG-fake"
    assert "CC-A" in body["snippet"]


def test_get_page_validates_params():
    s3 = _s3_with_two_pages()
    resp = h._get_page(_event("GET", "/mdreview/api/page", {"book": "bookX"}), s3, "b")
    assert resp["statusCode"] == 400


def test_save_writes_reviewed_key():
    s3 = _s3_with_two_pages()
    body = json.dumps({"book": "bookX", "page": 156, "markdown": "| a | b |"})
    resp = h._save_snippet(_event("POST", "/mdreview/api/save", body=body), s3, "b")
    assert resp["statusCode"] == 200
    assert s3.store["ocr-output/bookX/reviewed/p156.md"] == b"| a | b |"


def test_save_rejects_missing_fields():
    s3 = _s3_with_two_pages()
    body = json.dumps({"book": "bookX"})
    resp = h._save_snippet(_event("POST", "/mdreview/api/save", body=body), s3, "b")
    assert resp["statusCode"] == 400


def test_handler_serves_html(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "b")
    resp = h.handler(_event("GET", "/mdreview"), None)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "text/html"
    assert "OCR Markdown Review" in resp["body"]


def test_handler_unknown_route_404(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "b")
    resp = h.handler(_event("GET", "/nope"), None)
    assert resp["statusCode"] == 404


def test_handler_no_bucket_500(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    resp = h.handler(_event("GET", "/mdreview"), None)
    assert resp["statusCode"] == 500
