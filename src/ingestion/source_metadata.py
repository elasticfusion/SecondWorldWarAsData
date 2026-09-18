"""Source-metadata model: the recorded original document (source of truth).

One :class:`SourceMetadata` record is created per ingested source. It records
what the original document is, how it was obtained, and whether the pipeline
supports it, so that problems can be traced back and fixed later. All later
front-end stages (media detection, disposition classification, conversion) and
downstream citation reference this record.

See docs/current/dataquality/INGESTION_FRONT_END.md (step 1).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional

# Media types recognized by the front-end. "unsupported" is a recognized-but-
# unhandled bucket (see media_detection). Kept in sync with MediaType there.
MediaType = Literal["pdf", "html", "image", "moving_image", "unsupported"]

# Acquisition provenance: how the original was obtained.
AcquisitionMethod = Literal["download", "local", "unknown"]

_CHECKSUM_CHUNK = 1 << 20  # 1 MiB


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceMetadata:  # pylint: disable=too-many-instance-attributes
    """Recorded original document and its ingestion provenance.

    Attributes:
        source_id: Stable identifier for the source (ULID recommended, to match
            the entity store). Assigned by the caller.
        original_path: Retained path to the original document.
        media_type: Detected media type. Defaults to "unsupported" until a
            detector sets it.
        supported: Whether the pipeline supports this media. False for
            recognized-but-unsupported media (e.g. moving images today).
        acquisition_method: How the original was obtained.
        acquisition_url: Source URL/DOI when applicable.
        checksum: Content hash of the original (change detection / versioning).
            Prefixed with the algorithm, e.g. "sha256:abc...".
        detected_at: ISO-8601 UTC timestamp of when this record was created.
        notes: Free text (e.g. the reason a source is unsupported).
    """

    source_id: str
    original_path: Path
    media_type: MediaType = "unsupported"
    supported: bool = False
    acquisition_method: AcquisitionMethod = "unknown"
    acquisition_url: Optional[str] = None
    checksum: Optional[str] = None
    detected_at: str = field(default_factory=_utc_now_iso)
    notes: str = ""

    def __post_init__(self) -> None:
        # Accept str paths for convenience; normalize to Path.
        if not isinstance(self.original_path, Path):
            self.original_path = Path(self.original_path)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict (Path rendered as string)."""
        data = asdict(self)
        data["original_path"] = str(self.original_path)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceMetadata":
        """Rebuild a record from a dict produced by :meth:`to_dict`."""
        known = {f: data[f] for f in _FIELD_NAMES if f in data}
        return cls(**known)


# Field names computed once for from_dict filtering (tolerate extra keys).
_FIELD_NAMES = frozenset(f.name for f in fields(SourceMetadata))


def compute_checksum(path: Path, algorithm: str = "sha256") -> str:
    """Return "<algorithm>:<hexdigest>" for the file at ``path``.

    Reads in chunks so large originals (e.g. multi-hundred-MB PDFs) do not have
    to be loaded into memory.
    """
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHECKSUM_CHUNK), b""):
            digest.update(chunk)
    return f"{algorithm}:{digest.hexdigest()}"
