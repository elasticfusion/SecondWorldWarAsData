"""Durable artifact / document store (ARTIFACT_STORE_DESIGN.md).

ONE standard way to persist an acquired artifact (image, PDF, document, ...) so it
survives the ECS task and lands in S3. Layout under a SYNCED root:

    artifacts/<source>/<artifact_id>/
        original/<filename>     archival master (bytes exactly as acquired)
        manifest.json           provenance + sha256 + content_type + status + lineage

The local root is a SIBLING of the pipeline ``output/`` dir, so the existing ECS
S3 sync (which now also syncs ``artifacts/``) carries it to
``s3://{bucket}/artifacts/...``. A CloudFormation lifecycle rule transitions
``original/`` to Glacier Deep Archive once processed (NOT this module's concern).

Design decisions (owner): artifacts are stored UNCOMPRESSED (byte-faithful master;
the data is predominantly already-compressed imagery and Deep Archive is ~$1/TB-mo
so compression savings are immaterial). Fail-safe: a storage error never raises
into the caller — it returns None so enrichment proceeds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Local root for the store. In ECS the pipeline runs under /tmp/pipeline with
# output at /tmp/pipeline/output; artifacts/ is its sibling so the sync picks it
# up. Locally it resolves to ./artifacts. Callers may override via artifact_root().
_DEFAULT_ROOT = Path("artifacts")

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def artifact_root(output_root: Optional[Path] = None) -> Path:
    """Resolve the artifact store root. If ``output_root`` (the pipeline output
    dir) is given, the store is its sibling (``<output_root>/../artifacts``) so it
    rides the same workdir + S3 sync; otherwise falls back to ``./artifacts``."""
    if output_root is not None:
        return output_root.parent / "artifacts"
    return _DEFAULT_ROOT


def _safe(segment: str, fallback: str = "unknown") -> str:
    """Sanitize a path segment (source / id) to a safe, bounded token."""
    cleaned = _SAFE_SEGMENT.sub("_", (segment or "").strip()).strip("._-")
    return (cleaned or fallback)[:128]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_master(
    data: bytes,
    *,
    source: str,
    artifact_id: str,
    filename: str,
    content_type: str = "",
    provenance: Optional[Dict[str, Any]] = None,
    status: str = "stored",
    root: Optional[Path] = None,
    output_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Store an artifact master + manifest. Returns the manifest dict (with
    ``local_path`` relative to the store root) or None on any failure (fail-safe).

    - ``source``      logical origin bucket (e.g. "openserp", "nara", "wikipedia").
    - ``artifact_id`` stable id within the source (e.g. a GroupID or a checksum).
    - ``filename``    name for the stored master under ``original/``.
    - ``provenance``  free-form dict (url, entity id, query, license, ...).
    - ``status``      lifecycle status; the Deep-Archive lifecycle keys off this.
    """
    if not data:
        return None
    try:
        base = (root or artifact_root(output_root)).resolve()
        src = _safe(source, "unknown-source")
        aid = _safe(artifact_id, "unknown-id")
        fname = _safe(filename, "artifact")
        art_dir = base / src / aid
        orig_dir = art_dir / "original"
        orig_dir.mkdir(parents=True, exist_ok=True)

        master = orig_dir / fname
        master.write_bytes(data)

        checksum = _sha256(data)
        manifest: Dict[str, Any] = {
            "source": src,
            "artifact_id": aid,
            "original_filename": fname,
            "local_path": str(master.relative_to(base)),
            "sha256": checksum,
            "size_bytes": len(data),
            "content_type": content_type or "",
            "status": status,
            "stored_at": date.today().isoformat(),
            "provenance": provenance or {},
        }
        (art_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        logger.info(
            "artifact stored: %s/%s/original/%s (%d bytes, sha256=%s)",
            src,
            aid,
            fname,
            len(data),
            checksum[:12],
        )
        return manifest
    except Exception as e:  # noqa: BLE001 - storage must never block enrichment
        logger.warning("artifact store failed for %s/%s: %s", source, artifact_id, e)
        return None
