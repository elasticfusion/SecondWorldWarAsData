"""ONE place that knows where ``source_section`` records live.

The event pass writes them UNDER the content tree: ``output/content/<book>/source_section/<id>.json``.
An older/top-level layout (``output/source_section/*.json``) is still honoured. Several readers each
hard-coded ``out / "source_section"`` instead, found nothing in production and silently did nothing
(Phase 2 step 4b minted a ghost record + ghost-ID images; Phase 3 OpenSERP enrichment, image->place
links and the corpus-volume dedup all iterated an empty directory — XChannel-8 E2E, 2026-11-07).
Every reader/writer must go through this module so the layout is defined exactly once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, List

# The real layout first, the legacy one last (a stale legacy copy must never shadow the real record).
SECTION_GLOBS = ("content/*/source_section/*.json", "source_section/*.json")
_SKIP = {"index.json"}


def iter_section_files(out: Path) -> Iterator[Path]:
    """Every source_section record file under the output root (deduped, stable order)."""
    seen: set = set()
    for pattern in SECTION_GLOBS:
        for sf in sorted(out.glob(pattern)):
            if sf.name in _SKIP or sf in seen:
                continue
            seen.add(sf)
            yield sf


def section_dirs(out: Path) -> List[Path]:
    """Existing directories that hold source_section records, real layout first."""
    dirs: List[Path] = sorted(
        p for p in out.glob("content/*/source_section") if p.is_dir()
    )
    legacy = out / "source_section"
    if legacy.is_dir():
        dirs.append(legacy)
    return dirs
