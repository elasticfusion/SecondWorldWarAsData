"""In-document map links (multimodal gap M1): read each map with Grok vision, keep the answer.

HyperWar chapters link author-curated maps from the narrative, e.g.
``[Map VIII](maps/USA-E-XChannel-VIII.jpg)``. The parser resolves those links into
``parsed.json -> maps[]``; ``persist_map_entities`` makes each one a Map record (URL-keyed, with the
chapters that cite it). ``read_in_doc_maps`` then asks Grok vision what each map shows and stores the
answer ON that Map record (``map_depicted``: ``depicted`` + ``depicted_provenance``):

    link -> SSRF-safe image gate (+ ibiblio / configured host allowlist) -> Map record already read?
         -> Grok vision (map_vision prompt) -> depicted + provenance on the Map record

A map already read is never sent to vision again. Maps are no longer recycled as prose sources under
``contentrepository/map-<id>/`` (owner decision 2026-11-17: on ECS those files were never uploaded,
so every answer was lost, and Phase 2 paid to read the prose again). Fail-safe: never raises into the
phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from src.utils.observe import observe_warn  # noqa: E402
from src.grok_client import BatchModeCollecting, book_scope
from src.utils.batch_pending import Pending

# Hosts whose in-document map links we will follow. HyperWar/ibiblio is the curated corpus source;
# same-host-as-the-document is also allowed. Everything else is skipped (defense-in-depth beyond the
# SSRF IP gate — an in-doc link should point at the document's own asset host, not arbitrary sites).
_ALLOWED_MAP_HOSTS = ("ibiblio.org",)
# Per-document ceiling on maps read (cost: one Grok vision call each).
DEFAULT_MAX_LINKS_PER_DOC = 50
VISION_KIND = "in_doc_map_vision"


def _ssrf_safe_image_url(url: str):
    """Module-level handle to the shared SSRF image gate (patchable in tests)."""
    from src.extraction.search_external_maps import (
        _ssrf_safe_image_url as _impl,
    )

    return _impl(url)


def _host_allowed(url: str, extra_hosts: "tuple[str, ...]" = ()) -> bool:
    """True if the map URL's host is an allowlisted corpus host (ibiblio, plus operator-configured
    ``external_maps.allowed_hosts``). The document's OWN host is deliberately NOT trusted: the
    source URL is uploader-supplied metadata, so honouring it would let an upload widen the
    allowlist and make the pipeline fetch arbitrary images and send them to paid vision.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(
        host == h or host.endswith("." + h)
        for h in (*_ALLOWED_MAP_HOSTS, *(x.lower() for x in extra_hosts))
    )


def _vision_depicted(
    grok_client: Any,
    img_url: str,
    map_title: str,
    context: Dict[str, str],
    image_timeout: int,
) -> Optional[Dict[str, Any]]:
    """Run the map_vision verify+extract on a direct image URL. Returns Grok's answer (a dict) or
    None when there is no usable answer (an error: nothing is recorded, so it is asked again).
    Reuses the shared verification prompt."""
    from src.extraction.search_external_maps import _build_verification_prompt

    prompt = _build_verification_prompt(
        context.get("place_name", ""),
        context.get("date"),
        context.get("event_context", ""),
        map_title,
        context.get("alt_text", ""),
        context.get("caption", ""),
    )
    try:
        result = grok_client.extract_json_with_image(
            prompt=prompt,
            image_url=img_url,
            cache_type=VISION_KIND,
            image_timeout=image_timeout,
        )
    except BatchModeCollecting:
        raise  # queued for the batch, not an answer: the caller must not stamp or cache a verdict
    except Exception as e:  # noqa: BLE001 - a vision error must not crash the ingest
        observe_warn(
            "map_link",
            "vision_error",
            url=img_url[:120],
            err=type(e).__name__,
            note="no answer: nothing is recorded, so the map is asked about again next run",
        )
        return None
    return result if isinstance(result, dict) else None


def read_in_doc_maps(
    parsed_docs: List[Dict[str, Any]],
    grok_client: Any,
    maps_dir: Path,
    *,
    image_timeout: int = 30,
    allowed_hosts: "tuple[str, ...]" = (),
    max_per_doc: int = DEFAULT_MAX_LINKS_PER_DOC,
) -> Dict[str, int]:
    """For each parsed doc's ``maps[]`` (in-document map links) whose Map record exists
    (``persist_map_entities`` runs first) and has not been read, ask Grok vision what it shows and
    store the answer on the record. Returns a summary dict. Fail-safe; emits OBSERV map_link.*.

    LOOP GUARD: a document of a map-derived source (book=map-*, made before maps stopped being
    recycled) is skipped.
    """
    pending = Pending()
    summary = {"links": 0, "read": 0, "depicted": 0, "already": 0, "skipped": 0}
    url_index = _load_url_index(maps_dir / "index.json")
    for doc in parsed_docs:
        if not isinstance(doc, dict):
            continue
        book = str(doc.get("book", ""))
        if book.startswith("map-"):
            continue  # loop guard
        maps = [m for m in (doc.get("maps") or []) if isinstance(m, dict)]
        maps = _capped(maps, max_per_doc, book, summary)
        context = _doc_context(doc)
        with book_scope(book):  # the vision answer must echo THIS document's BookID
            for m in maps:
                summary["links"] += 1
                pending.call(  # a queued vision request: every link queues its own, raise once at the end
                    _read_one_link,
                    m,
                    _MapRead(grok_client, context, tuple(allowed_hosts), image_timeout),
                    _map_file(maps_dir, url_index, m),
                    book,
                    summary,
                )
    logger.info(
        "OBSERV map_link.summary links=%d read=%d depicted=%d already=%d skipped=%d",
        summary["links"],
        summary["read"],
        summary["depicted"],
        summary["already"],
        summary["skipped"],
    )
    pending.raise_if_queued()  # batch rounds: the step is pending, the next pass has the answers
    return summary


@dataclass(frozen=True)
class _MapRead:
    """What reading a map needs: the client, the document's context, the allowed hosts, timeout."""

    grok_client: Any
    context: Dict[str, str]
    allowed_hosts: "tuple[str, ...]"
    image_timeout: int


def _capped(
    maps: List[Dict[str, Any]], max_per_doc: int, book: str, summary: Dict[str, int]
) -> List[Dict[str, Any]]:
    if len(maps) <= max_per_doc:
        return maps
    # Cost cap: each map read is a paid vision call. Never silent.
    observe_warn(
        "map_link",
        "cap_hit",
        note=(
            f"this document has {len(maps)} map links; only the first {max_per_doc} are read "
            "(each costs a Grok vision call). Raise external_maps.max_recycled_per_doc if that "
            "many maps are expected, else check the document for junk links"
        ),
        book=book,
        links=len(maps),
        cap=max_per_doc,
    )
    summary["skipped"] += len(maps) - max_per_doc
    return maps[:max_per_doc]


def _map_file(
    maps_dir: Path, url_index: Dict[str, str], m: Dict[str, Any]
) -> Optional[Path]:
    """The Map record of an in-document map (by its URL), or None when there is none."""
    name = (
        url_index.get(str(m.get("url", "") or "").strip())
        if isinstance(url_index, dict)
        else None
    )
    if (
        not isinstance(name, str)
        or not name.endswith(".json")
        or name == "index.json"
        or "/" in name
        or "\\" in name
    ):
        return None
    f = maps_dir / name
    return f if f.is_file() else None


def _doc_context(doc: Dict[str, Any]) -> Dict[str, str]:
    """Minimal place/event/date context from the doc to steer map_vision relevance."""
    title = str(doc.get("chapter_title", "") or doc.get("book", ""))
    return {
        "place_name": "",
        "event_context": title,
        "date": "",
        "alt_text": "",
        "caption": title,
    }


def _read_one_link(
    m: Dict[str, Any],
    how: _MapRead,
    map_file: Optional[Path],
    book: str,
    summary: Dict[str, int],
) -> None:
    """Read one in-document map with vision and store the answer on its Map record (mutates
    ``summary``). Fail-safe."""
    from src.extraction import map_depicted

    url = str(m.get("url", ""))
    map_title = str(m.get("description", "") or f"Map {m.get('map_id') or ''}".strip())
    try:
        cleaned = _ssrf_safe_image_url(url)
        if not cleaned or not _host_allowed(cleaned, how.allowed_hosts):
            logger.info("OBSERV map_link.skipped url=%s reason=ssrf_or_host", url[:120])
            summary["skipped"] += 1
            return
        if map_file is None:
            observe_warn(
                "map_link",
                "no_map_record",
                url=cleaned[:120],
                note="the map has no Map record (persist_map_entities did not make one), so "
                "it is not read; see the maps.persist_summary line",
            )
            summary["skipped"] += 1
            return
        if map_depicted.was_read(_read_record(map_file)):
            summary["already"] += 1  # never sent to vision twice
            return
        answer = _vision_depicted(
            how.grok_client, cleaned, map_title, how.context, how.image_timeout
        )
        if answer is None:
            summary["skipped"] += 1
            return
        _store_answer(map_file, answer, cleaned, book, how.grok_client, summary)
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception as e:  # noqa: BLE001 - one bad link must not crash the ingest
        observe_warn("map_link", "error", url=url[:120], reason=str(e)[:120])
        summary["skipped"] += 1


def _read_record(map_file: Path) -> Any:
    import json as _json

    try:
        return _json.loads(map_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _store_answer(
    map_file: Path,
    answer: Dict[str, Any],
    image_url: str,
    book: str,
    grok_client: Any,
    summary: Dict[str, int],
) -> None:
    from src.extraction import map_depicted

    verdict = map_depicted.verdict_for(answer)
    depicted = answer.get("depicted")
    prov = map_depicted.provenance(
        verdict,
        kind=VISION_KIND,
        model=map_depicted.model_for(grok_client, VISION_KIND),
        request_id=map_depicted.answer_request_id(answer),
        image_url=image_url,
        found_in=book,
    )
    if not map_depicted.store(map_file, depicted, prov):
        summary["skipped"] += 1  # map_depicted said why
        return
    summary["read"] += 1
    if verdict == map_depicted.DEPICTED:
        summary["depicted"] += 1
    logger.info(
        "OBSERV map_link.read map=%s verdict=%s url=%s",
        map_file.stem,
        verdict,
        image_url[:120],
    )


def persist_map_entities(
    parsed_docs: List[Dict[str, Any]],
    maps_dir: Path,
    download: bool = True,
    output_root: Optional[Path] = None,
    grok_client: Any = None,
) -> Dict[str, int]:
    """Step 5a — persist a Map ENTITY per in-document map, keyed by URL, ACCUMULATING each chapter's
    reference footprint (roman-numeral campaign foldouts are shared across chapters/books). Reuses
    map_common.find_or_create_map_by_url (URL find-or-create + append) + base_map_record. Distinct
    from read_in_doc_maps (which stores what a map shows on this record): this writes the Map
    entity record + its cross-chapter references[]. Fail-safe; emits OBSERV maps.persist_summary.
    The url_index persists in maps_dir/index.json so accumulation survives across docs AND runs.
    """
    maps_dir.mkdir(parents=True, exist_ok=True)
    index_path = maps_dir / "index.json"
    url_index = _load_url_index(index_path)
    counts = {"created": 0, "existing": 0, "skipped": 0}
    pending = Pending()
    saved = len(url_index)
    for doc in parsed_docs:
        if not isinstance(doc, dict):
            continue
        book = str(doc.get("book", ""))
        if book.startswith("map-"):
            continue  # loop guard (mirror read_in_doc_maps)
        for m in doc.get("maps") or []:
            if isinstance(m, dict):
                pending.call(  # a queued vision request must not skip the index save below
                    _persist_one_map,
                    m,
                    book,
                    maps_dir,
                    url_index,
                    counts,
                    download,
                    output_root,
                    grok_client,
                )
        if len(url_index) != saved:
            # after a document that added maps (not only at the end): a task stopped midway keeps
            # the URLs it already mapped, so the next run does not create the same Map again
            _save_url_index(index_path, url_index)
            saved = len(url_index)
    _save_url_index(index_path, url_index)
    logger.info(
        "OBSERV maps.persist_summary created=%d existing=%d skipped=%d status=%s",
        counts["created"],
        counts["existing"],
        counts["skipped"],
        "warn" if counts["skipped"] else "ok",
    )
    pending.raise_if_queued()  # batch rounds: the step is pending, the next pass has the answers
    return counts


def _load_url_index(index_path: Path) -> Dict[str, str]:
    import json as _json

    if not index_path.exists():
        return {}
    try:
        return _json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a corrupt index is loud, rebuilt empty
        logger.warning(
            "OBSERV maps.index_read_failed reason=%s status=warn", type(e).__name__
        )
        return {}


def _save_url_index(index_path: Path, url_index: Dict[str, str]) -> None:
    # Sanctioned index write (matches places/dates/batch_parallel) — NOT a raw write_text.
    from src.utils.file_lock import write_json_with_lock

    try:
        write_json_with_lock(index_path, url_index)
    except Exception as e:  # noqa: BLE001 - index write failure is loud, not fatal
        logger.warning(
            "OBSERV maps.index_write_failed reason=%s status=warn", type(e).__name__
        )


def _persist_one_map(
    m: Dict[str, Any],
    book: str,
    maps_dir: Path,
    url_index: Dict[str, str],
    counts: Dict[str, int],
    download: bool = True,
    output_root: Optional[Path] = None,
    grok_client: Any = None,
) -> None:
    """Persist (find-or-create + append footprint) one in-document map. Mutates counts."""
    from src.extraction.map_common import (
        base_map_record,
        find_or_create_map_by_url,
        classify_map_type,
        download_map_image,
    )

    url = str(m.get("url", "") or "").strip()
    if not url:
        counts["skipped"] += 1
        return
    refs = _stamp_book(m.get("references") or [], book)
    before = url in url_index

    title = str(m.get("description", "") or m.get("map_id", ""))

    def _build(map_id: str) -> Dict[str, Any]:
        # Runs ONLY on CREATE (find_or_create calls build_record just for a new map) -> download +
        # classify happen once per map, never on a cross-chapter append (idempotent).
        local_image_path = file_format = None
        if download:
            local_image_path, file_format = download_map_image(
                url, map_id, maps_dir / "files"
            )
        return {
            **base_map_record(
                map_id=map_id,
                map_title=title,
                description=m.get("description"),
                map_type=classify_map_type(title),
                source_url=url,
                local_path=None,
                local_image_path=local_image_path,
                file_format=file_format,
                storage_backend="filesystem",
            ),
            "figure_number": m.get("map_id"),
            # A map routed from an image already vision-classified in Phase 3 carries its verdict,
            # so the tier-3 backstop does not re-submit it to Grok (don't-resubmit guarantee).
            **{
                k: m[k]
                for k in ("is_map", "map_vision_checked_at")
                if m.get(k) is not None
            },
        }

    f = find_or_create_map_by_url(
        url=url,
        maps_dir=maps_dir,
        url_index=url_index,
        references=refs,
        build_record=_build,
    )
    if f is None:
        counts["skipped"] += 1
        return
    if before:
        counts["existing"] += 1
    else:
        counts["created"] += 1
    # Step 5c-i: deterministic Place/Date/Organization linking from the footprint sub-events.
    if output_root is not None:
        _link_persisted_map(f, output_root, grok_client)


def _link_persisted_map(
    map_file: Path, output_root: Path, grok_client: Any = None
) -> None:
    """Read the persisted map, stamp Place/Date/Org links from its footprint, write back if changed.
    Fail-safe."""
    import json as _json

    from src.extraction.map_common import link_map_entities, maybe_vision_classify_map
    from src.utils.file_lock import write_json_with_lock

    try:
        rec = _json.loads(map_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - never block the phase on a link read
        logger.warning(
            "OBSERV maps.link_read_failed file=%s reason=%s status=warn",
            map_file.name,
            type(e).__name__,
        )
        return
    before = (
        rec.get("PlaceMentionID"),
        rec.get("DateMentionID"),
        rec.get("PeopleGroupID"),
        rec.get("is_map"),
        rec.get("map_vision_checked_at"),
    )
    link_map_entities(rec, output_root)

    # Tier classification (5c-ii): tiers 1/2 deterministic; the tier-3 vision backstop runs only for
    # AMBIGUOUS images, only if the image is downloaded locally, and ONLY once (idempotent).
    def _load_image():
        lip = rec.get("local_image_path")
        if lip and Path(lip).exists():
            return Path(lip).read_bytes()
        return None

    # The CALLER's client (the phase's rate-limited, cache-routed one); a private fallback only
    # when none was passed — never a process-cached None that silently disables vision.
    maybe_vision_classify_map(
        rec, _load_image, grok_client or _grok_client_for_vision()
    )
    after = (
        rec.get("PlaceMentionID"),
        rec.get("DateMentionID"),
        rec.get("PeopleGroupID"),
        rec.get("is_map"),
        rec.get("map_vision_checked_at"),
    )
    if after != before:
        write_json_with_lock(map_file, rec)


def _grok_client_for_vision():
    """Best-effort grok client for the tier-3 vision backstop. None -> deterministic tiers only
    (vision skipped). Fail-safe."""
    try:
        from src.grok_client import GrokClient

        return GrokClient(Path("cache/api"), batch_mode=False)
    except Exception:  # noqa: BLE001
        return None


def _stamp_book(references: List[Dict[str, Any]], book: str) -> List[Dict[str, Any]]:
    """Stamp the source `book` onto each reference footprint entry (cross-chapter/-book attribution
    + the dedup key). Preserves paragraph_number (mandatory) + Sub_eventID."""
    out = []
    for r in references:
        if not isinstance(r, dict):
            continue
        rr = dict(r)
        rr.setdefault("book", book)
        out.append(rr)
    return out
