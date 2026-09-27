"""Local-holdings recognition for bibliography resolution.

Before searching NARA / Archive.org / the web, check whether we ALREADY HOLD the
cited source (e.g. a citation to "MS # B-405" when ``unprocesseddocs/B405.pdf``
is in hand and being OCR'd/translated). Matching a local holding is the best
resolution: it points at our own authoritative artifact for attribution, needs
no online search or human disposition, and is free.

Especially relevant for the German Report Series (MS # A-/B-series), a
self-referential corpus whose citations reference documents in the same
collection we ingest.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default local directories that hold source documents.
DEFAULT_SOURCE_DIRS = (
    Path("unprocesseddocs"),
    Path("contentrepository"),
)

# MS-number series identifiers: "MS #B-405", "MS # A-105", "B-405", "M-502".
_MS_ID = re.compile(r"\bMS\s*#?\s*([A-Z])\s*-?\s*(\d{2,4})\b", re.IGNORECASE)
# Bare form, hyphen OPTIONAL so it matches both citations ("B-405") and the
# on-disk filename stem ("B405"). Require an uppercase letter + 3-4 digits to
# avoid matching arbitrary word+number tokens.
_BARE_ID = re.compile(r"\b([A-Z])-?(\d{3,4})\b")


def normalize_source_id(text: str) -> Optional[str]:
    """Extract + canonicalize an MS/report-series id from citation text.

    "MS # B-405" / "MS #B-405" / "B-405" -> "B405"; returns None if no id.
    """
    if not text:
        return None
    m = _MS_ID.search(text) or _BARE_ID.search(text)
    if not m:
        return None
    return f"{m.group(1).upper()}{int(m.group(2)):03d}"


def _index_dir(root: Path) -> Dict[str, str]:
    """Map canonical source-id -> file path for source docs under ``root``."""
    index: Dict[str, str] = {}
    if not root.exists():
        return index
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        canon = normalize_source_id(path.stem)
        if canon and canon not in index:
            index[canon] = str(path)
    return index


def build_holdings_index(
    source_dirs: Optional[List[Path]] = None,
) -> Dict[str, str]:
    """Build a canonical-id -> local-path index of held source documents."""
    dirs = source_dirs if source_dirs is not None else list(DEFAULT_SOURCE_DIRS)
    index: Dict[str, str] = {}
    for root in dirs:
        for canon, path in _index_dir(root).items():
            index.setdefault(canon, path)
    return index


def find_local_holding(entry: Dict, holdings_index: Dict[str, str]) -> Optional[str]:
    """Return the local path if we already hold this entry's cited source."""
    citation = entry.get("citation") or {}
    for text in (
        citation.get("title"),
        entry.get("verbatim_reference"),
        citation.get("verbatim"),
        entry.get("archive_reference_number"),
    ):
        canon = normalize_source_id(str(text or ""))
        if canon and canon in holdings_index:
            return holdings_index[canon]
    return None
