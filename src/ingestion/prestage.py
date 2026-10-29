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

# Decompression-bomb caps (P1-S3). An untrusted archive member must not be able to
# inflate unbounded. We reject on THREE independent signals, any one of which is a bomb
# signature: (1) declared uncompressed size over the per-member cap, (2) cumulative
# expanded bytes for the archive over the total cap, (3) compression ratio over the max
# (a tiny compressed member claiming/writing a huge payload). Enforcement is on BOTH the
# declared header AND the actual bytes written — a header that lies about its size is
# itself the bomb, so the bounded streaming copy aborts the moment actual bytes exceed
# the cap regardless of what the header claimed.
_PER_MEMBER_CAP = 500 * 1024 * 1024  # 500 MiB uncompressed per member
_CUMULATIVE_CAP = 2 * 1024 * 1024 * 1024  # 2 GiB total expanded per archive
_MAX_RATIO = 100  # uncompressed / compressed ratio ceiling
_COPY_CHUNK = 1024 * 1024  # 1 MiB bounded read window


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


def _bomb_reject(archive_name: str, member: str, detail: str) -> None:
    """Emit the LOUD OBSERV bomb-rejection WARNING. Fail-safe: never raises."""
    try:
        logger.warning(
            "OBSERV prestage.bomb_rejected archive=%s member=%s %s "
            "status=anomaly (decompression bomb — member skipped)",
            archive_name,
            member,
            detail,
        )
    except Exception:  # noqa: BLE001 - observability never breaks expansion
        pass


def _declared_reject_reason(
    declared_size: int, compress_size: int, cumulative: int
) -> Optional[str]:
    """Return a rejection reason if the member's DECLARED header trips a bomb cap, else None.

    Checks the per-member cap, the running cumulative cap, and the compression ratio
    (guarding compress_size==0, which would otherwise divide-by-zero and is itself
    suspicious — a 0-byte compressed member claiming real content)."""
    if declared_size > _PER_MEMBER_CAP:
        return "declared_size=%d over per-member cap=%d" % (
            declared_size,
            _PER_MEMBER_CAP,
        )
    if cumulative + declared_size > _CUMULATIVE_CAP:
        return "declared_size=%d would exceed cumulative cap=%d (running=%d)" % (
            declared_size,
            _CUMULATIVE_CAP,
            cumulative,
        )
    if compress_size <= 0:
        if declared_size > 0:
            return "ratio=inf (compress_size=0, declared_size=%d)" % declared_size
    else:
        ratio = declared_size / compress_size
        if ratio > _MAX_RATIO:
            return "ratio=%.1f over max=%d (declared_size=%d compress_size=%d)" % (
                ratio,
                _MAX_RATIO,
                declared_size,
                compress_size,
            )
    return None


def _copy_bounded(src, out, cumulative: int) -> int:
    """Stream src -> out in bounded chunks, enforcing the ACTUAL-bytes caps.

    Returns bytes written on success, or -1 if a cap was breached mid-copy (a lying
    header). On breach the caller is responsible for discarding the partial target."""
    written = 0
    while True:
        chunk = src.read(_COPY_CHUNK)
        if not chunk:
            break
        written += len(chunk)
        if written > _PER_MEMBER_CAP:
            return -1
        if cumulative + written > _CUMULATIVE_CAP:
            return -1
        out.write(chunk)
    return written


def _extract_member(
    open_member,
    filename: str,
    dest: Path,
    held_names: set,
    *,
    archive_name: str = "",
    declared_size: int = 0,
    compress_size: int = 0,
    cumulative: int = 0,
) -> Tuple[Optional[Path], int]:
    """Extract one archive member into dest (flattened, zip-slip safe, bomb-capped).

    Returns ``(path, bytes_written)``. ``path`` is None when the member is skipped
    (already held, empty name, or rejected as a decompression bomb); ``bytes_written``
    is 0 in that case. Fail-safe: a bomb member is skipped + logged, never raised."""
    name = Path(filename).name
    if not name or name in held_names:
        if name in held_names:
            logger.info("Skipping already-held %s", name)
        return None, 0

    reason = _declared_reject_reason(declared_size, compress_size, cumulative)
    if reason is not None:
        _bomb_reject(archive_name, name, reason)
        return None, 0

    target = dest / name
    with open_member() as src, open(target, "wb") as out:
        written = _copy_bounded(src, out, cumulative)
    if written < 0:
        # Header lied: actual bytes blew past the cap mid-stream. Discard the partial file.
        try:
            target.unlink()
        except OSError:
            pass
        _bomb_reject(
            archive_name,
            name,
            "actual bytes exceeded cap (declared_size=%d — lying header)"
            % declared_size,
        )
        return None, 0
    return target, written


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
            cumulative = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target, written = _extract_member(
                    lambda i=info, z=zf: z.open(i),
                    info.filename,
                    dest,
                    held_names,
                    archive_name=archive.name,
                    declared_size=info.file_size,
                    compress_size=info.compress_size,
                    cumulative=cumulative,
                )
                if target:
                    cumulative += written
                    extracted.append(target)
    elif suffix == ".rar":
        extracted.extend(_expand_rar(archive, dest, held_names))
    _observe_expansion(archive, extracted)
    return extracted


# Sane caps for observability (NOT enforcement — a separate guard blocks; this makes an
# oversized/over-count expansion LOUD so a decompression bomb is never silently absorbed).
_EXPAND_BYTES_WARN = 2 * 1024 * 1024 * 1024  # 2 GiB total expanded
_EXPAND_MEMBERS_WARN = 10000


def _observe_expansion(archive: Path, extracted: List[Path]) -> None:
    """Emit OBSERV prestage.expanded + a LOUD anomaly when the expansion is suspiciously large
    (decompression-bomb signal). Fail-safe: never raises into the pass."""
    try:
        total = 0
        for p in extracted:
            try:
                total += p.stat().st_size
            except OSError:
                continue
        n = len(extracted)
        base = "OBSERV prestage.expanded archive=%s members=%d bytes=%d" % (
            archive.name,
            n,
            total,
        )
        if total > _EXPAND_BYTES_WARN or n > _EXPAND_MEMBERS_WARN:
            logger.warning(
                base
                + " status=anomaly (expansion exceeds cap — possible decompression bomb)"
            )
        else:
            logger.info(base + " status=ok")
    except Exception:  # noqa: BLE001 - observability never breaks expansion
        pass


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
        cumulative = 0
        for info in rf.infolist():
            if getattr(info, "isdir", lambda: False)():
                continue
            target, written = _extract_member(
                lambda i=info, r=rf: r.open(i),
                info.filename,
                dest,
                held_names,
                archive_name=archive.name,
                declared_size=getattr(info, "file_size", 0) or 0,
                compress_size=getattr(info, "compress_size", 0) or 0,
                cumulative=cumulative,
            )
            if target:
                cumulative += written
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
    _observe_prestage_summary(summary)
    return summary


def _observe_prestage_summary(summary: Dict[str, int]) -> None:
    """Canonical prestage summary; a non-empty 'skip' (unsupported) track is a LOUD anomaly so
    dropped/unsupported files are visible, never silently summarized. Fail-safe."""
    try:
        skipped = summary.get("skip", 0)
        total = sum(summary.values())
        line = "OBSERV prestage.summary total=%d routed=%s skipped=%d" % (
            total,
            {k: v for k, v in summary.items() if k != "skip"},
            skipped,
        )
        if skipped:
            logger.warning(line + " status=anomaly (unsupported files -> needs-review)")
        else:
            logger.info(line + " status=ok")
    except Exception:  # noqa: BLE001 - observability never breaks prestage
        pass
