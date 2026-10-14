"""Artifact store + unit-patch acquisition (ARTIFACT_STORE_DESIGN.md M0)."""

import io
import json
from unittest.mock import MagicMock

import pytest

from src.enrichment import unit_patch
from src.utils import artifact_store


def _png_bytes(w: int, h: int) -> bytes:
    """A real decodable PNG of a given size (so PIL reports w*h)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


# --- artifact_store ---------------------------------------------------------


def test_store_master_writes_original_and_manifest(tmp_path):
    data = b"hello-master-bytes"
    m = artifact_store.store_master(
        data,
        source="openserp",
        artifact_id="01GROUPID",
        filename="unit_patch.jpg",
        content_type="image",
        provenance={"url": "https://ex.com/p.jpg"},
        root=tmp_path,
    )
    assert m is not None
    master = tmp_path / "openserp" / "01GROUPID" / "original" / "unit_patch.jpg"
    assert master.read_bytes() == data
    man = json.loads(
        (tmp_path / "openserp" / "01GROUPID" / "manifest.json").read_text()
    )
    import hashlib

    assert man["sha256"] == hashlib.sha256(data).hexdigest()
    assert man["size_bytes"] == len(data)
    assert man["local_path"] == "openserp/01GROUPID/original/unit_patch.jpg"
    assert man["provenance"]["url"] == "https://ex.com/p.jpg"


def test_store_master_sanitizes_segments(tmp_path):
    m = artifact_store.store_master(
        b"x", source="../evil", artifact_id="a/b/c", filename="x.jpg", root=tmp_path
    )
    assert m is not None
    # no path traversal — stays under tmp_path
    stored = (tmp_path / m["local_path"]).resolve()
    assert str(stored).startswith(str(tmp_path.resolve()))


def test_store_master_empty_is_none(tmp_path):
    assert (
        artifact_store.store_master(
            b"", source="s", artifact_id="i", filename="f", root=tmp_path
        )
        is None
    )


def test_artifact_root_is_sibling_of_output(tmp_path):
    out = tmp_path / "output"
    assert artifact_store.artifact_root(out) == tmp_path / "artifacts"


# --- unit_patch -------------------------------------------------------------


def test_acquire_patch_keeps_largest_confirmed(tmp_path, monkeypatch):
    small = _png_bytes(50, 50)
    large = _png_bytes(300, 300)
    gc = MagicMock()
    gc.extract_json_with_image.return_value = {"is_patch": True}  # all confirmed
    bytes_by_url = {"http://s/small.png": small, "http://s/large.png": large}
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: u)
    monkeypatch.setattr(unit_patch, "_download", lambda url: bytes_by_url[url])

    rec = unit_patch.acquire_unit_patch(
        [
            {"url": "http://s/small.png", "title": "a"},
            {"url": "http://s/large.png", "title": "b"},
        ],
        group_id="01G",
        unit_name="28th Infantry Division",
        nationality="USA",
        grok_client=gc,
        output_root=tmp_path / "output",
    )
    assert rec is not None
    assert rec["url"] == "http://s/large.png"  # largest wins
    assert rec["width"] == 300 and rec["height"] == 300
    assert rec["vision_verified"] is True
    # stored under the artifacts/ sibling
    stored = tmp_path / "artifacts" / rec["local_path"]
    assert stored.exists() and stored.read_bytes() == large


def test_acquire_patch_vision_fail_closed(tmp_path):
    # No grok client -> fail-closed -> nothing stored.
    rec = unit_patch.acquire_unit_patch(
        [{"url": "http://s/x.png"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=None,
        output_root=tmp_path / "output",
    )
    assert rec is None


def test_acquire_patch_none_confirmed_stores_nothing(tmp_path, monkeypatch):
    gc = MagicMock()
    gc.extract_json_with_image.return_value = {"is_patch": False}  # vision rejects all
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: u)
    monkeypatch.setattr(unit_patch, "_download", lambda url: _png_bytes(100, 100))
    rec = unit_patch.acquire_unit_patch(
        [{"url": "http://s/x.png"}, {"url": "http://s/y.png"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=gc,
        output_root=tmp_path / "output",
    )
    assert rec is None
    assert not (tmp_path / "artifacts").exists()


def test_acquire_patch_non_image_rejected(tmp_path, monkeypatch):
    # Vision says patch, but the bytes aren't a decodable image -> skip (not stored).
    gc = MagicMock()
    gc.extract_json_with_image.return_value = {"is_patch": True}
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: u)
    monkeypatch.setattr(unit_patch, "_download", lambda url: b"not-an-image")
    rec = unit_patch.acquire_unit_patch(
        [{"url": "http://s/x.png"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=gc,
        output_root=tmp_path / "output",
    )
    assert rec is None


def test_acquire_patch_vision_error_fail_closed(tmp_path, monkeypatch):
    gc = MagicMock()
    gc.extract_json_with_image.side_effect = RuntimeError("vision down")
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: u)
    monkeypatch.setattr(unit_patch, "_download", lambda url: _png_bytes(100, 100))
    rec = unit_patch.acquire_unit_patch(
        [{"url": "http://s/x.png"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=gc,
        output_root=tmp_path / "output",
    )
    assert rec is None


def test_acquire_patch_rejects_ssrf_url(tmp_path, monkeypatch):
    """SSRF: a candidate URL whose host resolves to a private/metadata IP must be
    rejected BEFORE any vision call or download (no fetch of 169.254.169.254 etc.)."""
    gc = MagicMock()
    gc.extract_json_with_image.return_value = {"is_patch": True}
    vision_calls = []
    gc.extract_json_with_image.side_effect = lambda **k: vision_calls.append(k) or {
        "is_patch": True
    }
    # _safe_url returns None for the unsafe host -> candidate skipped entirely.
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: None)
    dl_calls = []
    monkeypatch.setattr(
        unit_patch, "_download", lambda u: dl_calls.append(u) or _png_bytes(100, 100)
    )
    rec = unit_patch.acquire_unit_patch(
        [{"url": "http://169.254.169.254/latest/meta-data/"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=gc,
        output_root=tmp_path / "output",
    )
    assert rec is None
    assert vision_calls == []  # never asked Grok to fetch it
    assert dl_calls == []  # never downloaded it


def test_acquire_patch_requires_synced_root(tmp_path, monkeypatch):
    """output_root=None (e.g. S3-storage SQS worker) -> SKIP, never write a master
    to an unsynced ./artifacts (would be a durable dangling pointer)."""
    gc = MagicMock()
    gc.extract_json_with_image.return_value = {"is_patch": True}
    monkeypatch.setattr(unit_patch, "_safe_url", lambda u: u)
    monkeypatch.setattr(unit_patch, "_download", lambda u: _png_bytes(100, 100))
    rec = unit_patch.acquire_unit_patch(
        [{"url": "https://ex.com/p.png"}],
        group_id="01G",
        unit_name="U",
        nationality="USA",
        grok_client=gc,
        output_root=None,
    )
    assert rec is None
