"""Input pre-stage (M1 / CONCURRENCY_AND_NAT_SPEC §7).

Runs BEFORE the concurrency dispatcher. Turns the raw heterogeneous backlog
(238 PDFs, 204 zips + 2 rars, 184 JPGs) into individual, media-routed work items,
each with a `doc#` lifecycle record the dispatcher enumerates:

1. Archive expansion — unpack zip/rar to individual files. Resumable (an already
   -expanded archive is skipped) and holdings-deduped (skip files we already hold).
2. Media routing — classify each file (reusing media_detection) and map it to a
   work TRACK + the phase the dispatcher should launch first.

Pure/testable: expansion + routing are separable from AWS; doc# writes go through
doc_lifecycle (mockable).
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.ingestion import doc_lifecycle
from src.ingestion.media_detection import detect_media_type

logger = logging.getLogger(__name__)

# Media type -> (work track, first dispatcher phase). §7 routing.
#   scanned/native PDF both start "phase1" (Phase 0/1 handle OCR-vs-text internally);
#   images route to the vision track (not the narrative pipeline);
#   docx/epub/text -> text-convert -> narrative.
_ROUTING: Dict[str, Tuple[str, str]] = {
    "pdf": ("narrative", "phase1"),
    "docx": ("narrative", "phase1"),
    "epub": ("narrative", "phase1"),
    "text": ("narrative", "phase1"),
    "html": ("narrative", "phase1"),
    "image": ("vision", "phase1"),
    "moving_image": ("av", "phase1"),
    "unsupported": ("skip", "phase1"),
}

_ARCHIVE_EXTS = {".zip", ".rar"}


def _doc_id_for(path: Path) -> str:
    """Stable doc id from the file's path + size (cheap, collision-resistant enough)."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    h = hashlib.sha256(f"{path.name}:{size}".encode()).hexdigest()[:16]
    return f"{path.stem[:40]}-{h}"


def route_file(path: Path) -> Tuple[str, str, str]:
    """Return (media_type, track, next_phase) for a single file (§7)."""
    media = detect_media_type(path)
    track, phase = _ROUTING.get(media, ("skip", "phase1"))
    return media, track, phase


def _extract_member(
    open_member, filename: str, dest: Path, held_names: set
) -> Optional[Path]:
    """Extract one archive member into dest (flattened, zip-slip safe). None if skipped."""
    name = Path(filename).name
    if not name or name in held_names:
        if name in held_names:
            logger.info("Skipping already-held %s", name)
        return None
    target = dest / name
    with open_member() as src, open(target, "wb") as out:
        out.write(src.read())
    return target


def expand_archive(
    archive: Path, dest: Path, *, held_names: Optional[set] = None
) -> List[Path]:
    """Unpack a zip/rar archive into ``dest``, skipping already-held files.

    Returns the list of extracted file paths. rar requires the optional
    ``rarfile`` dependency; a rar without it is logged and skipped (resumable — a
    later run with the dep can complete it). Nested archives are returned as-is
    for a subsequent expansion pass (expansion is iterative).
    """
    held_names = held_names or set()
    extracted: List[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()

    if suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = _extract_member(
                    lambda i=info, z=zf: z.open(i), info.filename, dest, held_names
                )
                if target:
                    extracted.append(target)
    elif suffix == ".rar":
        extracted.extend(_expand_rar(archive, dest, held_names))
    logger.info("Expanded %s -> %d files", archive.name, len(extracted))
    return extracted


def _expand_rar(archive: Path, dest: Path, held_names: set) -> List[Path]:
    """Expand a rar archive if the optional rarfile dep is present (else skip)."""
    out: List[Path] = []
    try:
        import rarfile  # type: ignore  # optional dep
    except ImportError:
        logger.warning(
            "rar archive %s needs the 'rarfile' dep — skipping (resumable)",
            archive.name,
        )
        return out
    with rarfile.RarFile(archive) as rf:
        for info in rf.infolist():
            if getattr(info, "isdir", lambda: False)():
                continue
            target = _extract_member(
                lambda i=info, r=rf: r.open(i), info.filename, dest, held_names
            )
            if target:
                out.append(target)
    return out


def prestage_file(path: Path, *, book: str = "") -> Optional[str]:
    """Route a single non-archive file and create its doc# record. Returns doc_id.

    Files routed to the 'skip' track (unsupported) are recorded as needs-review so
    an operator sees them, not silently dropped. Returns None for that case's id
    only if you want to filter; here we always return the id for traceability.
    """
    media, track, phase = route_file(path)
    doc_id = _doc_id_for(path)
    status = "needs-review" if track == "skip" else "held_unprocessed"
    doc_lifecycle.upsert(
        doc_id,
        status=status,
        book=book or path.stem,
        media_type=media,
        track=track,
        next_phase=phase,
        source_path=str(path),
    )
    return doc_id


def prestage_dir(
    root: Path, expand_dest: Path, *, held_names: Optional[set] = None
) -> Dict[str, int]:
    """Pre-stage a directory: expand archives, then route every leaf file.

    Returns a summary count per track. Archive expansion is iterative (an archive
    inside an archive is expanded on the next pass). Idempotent at the doc# level
    (upsert), and archive expansion skips held files.
    """
    held_names = held_names or set()
    summary: Dict[str, int] = {}

    # Pass 1: expand archives (one level; nested archives get re-queued).
    to_route: List[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in _ARCHIVE_EXTS:
            for f in expand_archive(p, expand_dest, held_names=held_names):
                if f.suffix.lower() in _ARCHIVE_EXTS:
                    # nested archive — expand again next
                    to_route.extend(
                        expand_archive(f, expand_dest, held_names=held_names)
                    )
                else:
                    to_route.append(f)
        else:
            to_route.append(p)

    # Pass 2: route + record each file.
    for f in to_route:
        _, track, _ = route_file(f)
        prestage_file(f)
        summary[track] = summary.get(track, 0) + 1

    logger.info("Pre-stage summary: %s", summary)
    return summary
