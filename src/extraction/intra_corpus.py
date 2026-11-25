"""Intra-corpus document links (M8): citations of OTHER ibiblio HyperWar volumes, indexed.

HyperWar chapters cite OTHER volumes - "Utah Beach to Cherbourg", "Omaha Beachhead", "Small Unit
Actions" - and cross-reference pages within the same volume ("#page329"). ``process_doc_links``
classifies every link of a parsed document (``classify_link``) and returns a corpus-graph edge for each
in-corpus one, attributed to the citing SourceSectionID. The cross-volume edges are kept in the
citation index (``src/extraction/corpus_links``): every volume cited, by whom, at which page, and
whether we hold it yet - the list of volumes to ingest and of the records to update when each arrives.

History (owner 2026-11-24): the original M8 entry (2026-10-27, commit 6bd08a9d) asked only to mark these
citations as internal links. The implementation (bb4ac30b, PR #594) also fetched the ONE cited page and
uploaded it as its own book (``intracorpus-<volume>-<sha12>``, a permanent slug), citing an unrecorded owner
directive to queue cited documents. That made each cited page a duplicate of the volume to come, started
Phase 1->2->3 (Grok spend) without approval, and - because a book whose source URL is its volume's
``index.html`` was not recognised as that volume - would have re-uploaded a book's own pages. The upload is
removed: every volume will be ingested (owner), under its own name, from the ingestion list.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from src.utils.observe import observe_warn  # noqa: E402

# a volume's own index page (``.../hyperwar/USA/USA-E-XChannel/index.html`` or ``.../USA-E-XChannel/``):
# the source URL most HyperWar books are imported from. The volume key has a hyphen
# (``USA-E-XChannel``), so a section index such as ``/hyperwar/USA/index.html`` is not a volume.
_VOLUME_INDEX = re.compile(
    r"https?://[^\s]*?/hyperwar/(?:[^\s/]+/)*?(?P<vol>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)/"
    r"(?:index\.html?)?(?:[#?][^\s]*)?$",
    re.I,
)


def _volume_of(url: str) -> Optional[str]:
    """The HyperWar volume key for a URL, or None if it is not a recognizable in-corpus volume
    page: a page of the volume (the authoritative hyperwar_biblio recognizer) or its index page.
    Fail-safe."""
    try:
        from src.enrichment.hyperwar_biblio import _parse_volume

        url = (url or "")[:2048]  # both recognisers are bounded on an untrusted link
        parsed = _parse_volume(url)
        if parsed:
            return parsed[0]
        m = _VOLUME_INDEX.match(url)
        return m.group("vol") if m else None
    except Exception:  # noqa: BLE001
        return None


def classify_link(url: str, source_url: str) -> str:
    """Classify a resolved link relative to the citing document's ``source_url``:
      ``same_volume_anchor`` — a #fragment or a page in the SAME in-corpus volume (intra-doc nav);
      ``in_corpus_doc``      — a DIFFERENT in-corpus HyperWar volume document (ingestable);
      ``external``           — anything else.
    Pure; fail-safe -> 'external'."""
    try:
        target_vol = _volume_of(url)
        if target_vol is None:
            # a bare same-document "#anchor" is intra-corpus navigation within this volume
            return "same_volume_anchor" if url.startswith("#") else "external"
        src_vol = _volume_of(source_url)
        if src_vol is not None and src_vol == target_vol:
            return "same_volume_anchor"
        return "in_corpus_doc"
    except Exception:  # noqa: BLE001
        return "external"


def _section_volumes(sf: Path) -> set:
    """Volume key(s) a source_section file represents (from source.book / source_url). Fail-safe."""
    import json

    out: set = set()
    try:
        rec = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    src = (rec.get("source") or {}) if isinstance(rec, dict) else {}
    for cand in (src.get("book"), src.get("source_url")):
        v = _volume_of(str(cand or "")) or (str(cand) if cand else "")
        if v:
            out.add(v)
    return out


def _corpus_volumes_present(out: Path) -> set:
    """Volume keys already represented in the corpus (from the source_section records). Used to
    dedup: we don't re-queue a volume we already have. Fail-safe."""
    vols: set = set()
    try:
        from src.utils.source_sections import iter_section_files

        for sf in iter_section_files(out):
            vols |= _section_volumes(sf)
    except Exception as e:  # noqa: BLE001
        logger.debug("corpus-volumes scan skipped: %s", e)
    return vols


def _edge(
    from_section_id: str, url: str, kind: str, target_vol: Optional[str]
) -> Dict[str, Any]:
    """An internal corpus-graph edge with ULID attribution back to the citing source_section."""
    return {
        "from_SourceSectionID": from_section_id or None,
        "to_url": url,
        "to_volume": target_vol,
        "to_page": _page_anchor(url),
        "kind": kind,  # 'same_volume_anchor' | 'in_corpus_doc'
    }


def _page_anchor(url: str) -> Optional[str]:
    """The printed-page anchor a link points at (``#page329`` -> ``page329``), if any."""
    frag = (url or "").partition("#")[2]
    return frag[:40] if frag.lower().startswith("page") else None


def process_doc_links(
    content: str,
    source_url: str,
    book: str,
    *,
    book_to_section: Dict[str, str],
    quiet: bool = False,
) -> Dict[str, Any]:
    """For one parsed doc (``quiet``: one note of it - no summary line): classify every link and return the internal corpus-graph edges
    (ULID-attributed). Returns {edges, anchors, cited}: ``cited`` counts the links to another
    volume. Nothing is fetched or uploaded (the citation index records them). Fail-safe.
    """
    edges: List[Dict[str, Any]] = []
    counts = {"anchors": 0, "cited": 0}
    error = False
    try:
        from src.extraction.link_assets import extract_link_assets

        section_id = book_to_section.get((book or "").strip().lower(), "")
        for la in extract_link_assets(content, source_url):
            kind = classify_link(la.url, source_url)
            if kind == "external":
                continue
            edges.append(_edge(section_id, la.url, kind, _volume_of(la.url)))
            counts["anchors" if kind == "same_volume_anchor" else "cited"] += 1
    except Exception as e:  # noqa: BLE001 - must never crash the pipeline
        error = True
        observe_warn("intra_corpus", "error", reason=str(e)[:120])
    log = logger.debug if quiet else logger.info
    log(
        "OBSERV intra_corpus.summary book=%r edges=%d anchors=%d cited=%d",
        str(book)[:80],
        len(edges),
        counts["anchors"],
        counts["cited"],
    )
    return {"edges": edges, **counts, "error": error}
