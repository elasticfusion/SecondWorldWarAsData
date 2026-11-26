"""The copies ``bibliography_resolve`` finds go to the human review queue (Group 3, owner 2026-11-17).

A resolver finds a candidate copy of a cited work: an NARA catalog record, an Archive.org item, a
Project Gutenberg ebook, a page OpenSERP or the Grok LLM suggested. Grok has already judged it
against the citation. It is NOT written to the record: ``find_queue.enqueue_find`` holds it for a
human (provider ``nara-find`` / ``archive-org-find`` / ``gutenberg-find`` / ``openserp-find`` /
``llm-find``), tied to the record's ``BibliographyID`` and the citation it was found for, and the
record waits as ``search_status = "pending"``.

A NARA find also carries what a download would fetch (its ``digitalObjects``), found once when the
find is queued, so the reviewer sees what approving it would bring in. A human starts the download.

On a later run ``finds_index`` + ``approved_finds`` turn the review decisions back into the record:
an approved find becomes a ``resource_urls`` entry (the resolver does that, so one code path writes
them), and a record whose every find was rejected is searched again as ``not_found``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.utils import find_queue
from src.utils import fetch_provenance as fp

logger = logging.getLogger(__name__)

# the resolver's source label -> the provider the find is queued under (one per finder, so a
# reviewer sees where it came from)
SOURCE_PROVIDER = {
    "nara_catalog": "nara-find",
    "archive_org": "archive-org-find",
    "gutenberg": "gutenberg-find",
    "openserp_archive": "openserp-find",
    "openserp": "openserp-find",
    "llm": "llm-find",
}
NARA_SOURCE = "nara_catalog"
# a reviewer must be shown the item's own page, never a search or metadata API address
_NOT_A_PAGE = re.compile(
    r"advancedsearch\.php|/metadata/|/api/|/records/search|gutendex\.com", re.I
)
_SAMPLE_OBJECTS = (
    20  # object URLs kept on the find (the download step reads the full list again)
)


@dataclass
class Queued:
    """What happened to the finds of one record in one call."""

    queued: int = 0  # newly stored
    waiting: int = 0  # stored earlier and still pending, approved or submitted
    rejected: int = 0  # stored earlier and rejected by a human: never shown again
    refused: int = 0  # not queueable (no Grok verdict, unsafe URL, not a page, no ID)
    failed: int = 0  # the queue could not store it (tried again next run)

    @property
    def live(self) -> int:
        """Finds a human can still act on (or has approved)."""
        return self.queued + self.waiting

    @property
    def wait_for_review(self) -> bool:
        """The record waits for a human: a find is in the queue and none failed to be stored (a
        failed one is an outage: the record is searched again next run, and the stored finds then
        cost nothing)."""
        return self.live > 0 and self.failed == 0


def _summary(entry: Dict[str, Any], source: str) -> str:
    cit = entry.get("citation") or {}
    what = str(cit.get("title") or entry.get("verbatim_reference") or "")[:120]
    return f"{source} copy for bibliography record: {what}"


# naId -> its catalog reply, for this process
_LOOKED_UP: Dict[str, Dict[str, Any]] = {}
# (naId, citation, scan-list fingerprint) -> vision verdict, for this process
_VISION: Dict[tuple, Dict[str, Any]] = {}
_MEMO_MAX = 256  # records remembered per process (oldest forgotten first)


def _remember(memo: Dict[Any, Any], key: Any, value: Any) -> Any:
    if key not in memo and len(memo) >= _MEMO_MAX:
        memo.pop(next(iter(memo)))
    memo[key] = value
    return value


def _looked_up(naid: str, api_key: str) -> Optional[Dict[str, Any]]:
    """One catalog call per NARA record per process: a batch round repeats the pass that queued
    the vision request, and must not spend NARA quota again. A failed lookup is not remembered.
    """
    from src.enrichment import acquisition_fetchers as af

    if naid not in _LOOKED_UP:
        found = af.nara_digital_objects(naid, api_key)
        if found is None:
            return None
        _remember(_LOOKED_UP, naid, found)
    return _LOOKED_UP[naid]


def nara_download_details(
    url: str,
    api_key: Optional[str],
    citation: str = "",
    grok_client: Any = None,
) -> Dict[str, Any]:
    """What approving a NARA find would download: the record's page scans. One catalog API call.
    ``object_count`` is None (not 0) when the list could not be read, so a reviewer is never told
    "no scans" by an outage; the download step reads the list again."""
    from src.enrichment import acquisition_fetchers as af

    from urllib.parse import urlparse

    on_nara = (urlparse(url).hostname or "").lower() == "catalog.archives.gov"
    naid = af._nara_naid_from_urls([url]) if on_nara else ""  # noqa: SLF001
    details: Dict[str, Any] = {"naId": naid or None, "object_count": None}
    if not naid or not api_key:
        return details
    found = _looked_up(naid, api_key)
    if found is None:
        return details
    details.update(_scan_details(found))
    if found.get("scans"):
        details["vision"] = _vision(naid, citation, found, details, grok_client)
    return details


def _vision(naid, citation, found, details, grok_client) -> Dict[str, Any]:
    """Grok vision on the pages themselves (the description match alone can pass a neighbouring
    file), once per record, citation and scan list per process: the resolver may queue the same
    find twice in a pass, and its pages are not fetched again. Raises BatchModeCollecting when
    the request is queued for a batch (nothing is remembered then)."""
    from src.enrichment import nara_vision

    key = (naid, citation, found.get("manifest_sha256"))
    if key not in _VISION:
        verdict = nara_vision.check_scans(
            found["scans"], citation, details, grok_client
        )
        _remember(_VISION, key, verdict)
    return _VISION[key]


def _location(found: Dict[str, Any]) -> Dict[str, Any]:
    """Where it is held: record group, series, boxes/reels, reading room (the pull slip)."""
    return {
        "nara_title": found["title"],
        "level": found.get("level") or None,
        "series_path": found.get("series_path") or None,
        "record_group_number": found.get("record_group_number"),
        "series": found.get("series"),
        "containers": found.get("containers") or None,
        "reference_unit": found.get("reference_unit"),
    }


def _scan_details(found: Dict[str, Any]) -> Dict[str, Any]:
    """The scans of a NARA record as a reviewer sees them: a sample with each file's name, type
    and size, the total, the types, and the list's fingerprint (a download fetches exactly this
    list). A series' own list is not its scans (they sit on its items), so an empty one is unknown
    (None), never 0."""
    objs = found["object_urls"]
    known = found.get("scans_known", True) or bool(objs)
    return {
        **_location(found),
        "object_count": len(objs) if known else None,
        "object_urls": objs[:_SAMPLE_OBJECTS],
        "formats": sorted({o.lower().rsplit(".", 1)[-1][:5] for o in objs if "." in o}),
        "scans": (found.get("scans") or [])[:_SAMPLE_OBJECTS],
        "total_bytes": found.get("total_bytes"),
        "types": found.get("types") or None,
        "manifest_sha256": found.get("manifest_sha256"),
        "refused_urls": found.get("refused") or 0,
    }


def _existing(
    finds: Optional[List[Dict[str, Any]]], provider: str, url: str
) -> Optional[Dict[str, Any]]:
    """The stored event of this find in the record's loaded finds (None when absent)."""
    from src.utils.url_hygiene import clean_url

    clean = clean_url(url)
    return next(
        (
            e
            for e in finds or []
            if e.get("provider") == provider and e.get("endpoint") == clean
        ),
        None,
    )


def queue_findings(
    entry: Dict[str, Any],
    findings: "List[Tuple[str, str]]",
    *,
    query: str,
    grok_client: Any,
    config: Dict[str, Any],
    details: Optional[Dict[str, Any]] = None,
) -> Queued:
    """Queue each ``(url, source)`` finding of one record for human review. Never raises.

    ``details``: metadata the finder read (Archive.org's PDF link and page count), kept on the find
    and applied to the record only if it is approved. Without a Grok client nothing is queued: a
    find is shown to a human only after Grok has judged it."""
    out = Queued()
    origin = (
        find_queue.entity_origin("bibliography", entry, query=query)
        if grok_client is not None
        else None
    )
    if (
        origin is None
    ):  # no Grok verdict, or no ULID: nothing is queued, nothing looked up
        out.refused = len(findings)
        return out
    mine = (config.get("bib_finds") or {}).get(origin.entity_id)
    for url, source in findings:
        _queue_one(entry, origin, url, source, grok_client, config, details, mine, out)
    return out


def _provider_for(source: str, url: str) -> Optional[str]:
    """The find provider of a resolver source, or None (logged) when the find must not be queued:
    the source has no provider, or the URL is an API address and not the item's page."""
    provider = SOURCE_PROVIDER.get(source)
    if provider is not None and not _NOT_A_PAGE.search(url or ""):
        return provider
    logger.error(
        "OBSERV bib_finds.not_a_find source=%r url=%r status=error - not queued: the source "
        "has no find provider, or the URL is an API address and not the item's page. Fix "
        "the finder (bibliography_finds.SOURCE_PROVIDER)",
        find_queue.clean_for_log(source, 40),
        find_queue.clean_for_log(url, 120),
    )
    return None


def _earlier(mine, provider: str, url: str, origin) -> Optional[str]:
    """``"rejected"`` / ``"waiting"`` when an earlier run stored this find, else None. Without a
    loaded index any stored event counts as waiting (the conservative reading)."""
    seen = _existing(mine, provider, url)
    if seen is not None:
        return "rejected" if seen.get("review_status") == fp.REJECTED else "waiting"
    # not in the loaded index (queued earlier this run, or no index): ask the store
    if find_queue.already_queued(provider, url, origin):
        return "waiting"
    return None


_COUNTER = {
    find_queue.ENQUEUED: "queued",
    find_queue.ALREADY_QUEUED: "waiting",
    find_queue.FAILED: "failed",
}


def _queue_one(
    entry, origin, url, source, grok_client, config, details, mine, out: Queued
) -> None:
    provider = _provider_for(source, url)
    if provider is None:
        out.refused += 1
        return
    earlier = _earlier(mine, provider, url, origin)
    if earlier is not None:
        setattr(out, earlier, getattr(out, earlier) + 1)
        return
    params: Dict[str, Any] = {
        "search_source": source,
        "BibliographyID": origin.entity_id,
        "source_metadata": details or None,
    }
    if source == NARA_SOURCE:
        params["download"] = nara_download_details(
            url, config.get("nara_api_key"), origin.query, grok_client
        )
        if _vision_says_not_the_document(params["download"], url):
            out.refused += 1
            return
    status = find_queue.enqueue_find(
        provider=provider,
        url=url,
        verdict=find_queue.RELEVANT,  # every finder Grok-verifies before it returns a find
        origin=origin,
        summary=_summary(entry, source),
        params=params,
    )
    field = _COUNTER.get(status, "refused")
    setattr(out, field, getattr(out, field) + 1)


def _vision_says_not_the_document(details: Dict[str, Any], url: str) -> bool:
    """Grok vision looked at the record's pages and none belongs to the cited document: the find
    is not queued (no link reaches a human that Grok called irrelevant)."""
    from src.enrichment.nara_vision import NOT_MATCH

    vision = details.get("vision") or {}
    if vision.get("verdict") != NOT_MATCH:
        return False
    logger.info(
        "OBSERV bib_finds.nara_scans_not_the_document url=%s reason=%r",
        find_queue.clean_for_log(url),
        find_queue.clean_for_log(vision.get("reason") or "", 200),
    )
    return True


def finds_index() -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Every stored bibliography find by BibliographyID, or None when the store could not be read
    (then nothing is reconciled this run: a failed scan must not read as "no finds")."""
    from src.enrichment import fetch_review

    events, errors = fetch_review.load_events()
    if errors:
        logger.error(
            "OBSERV bib_finds.index_unreadable errors=%d status=error - bibliography finds are "
            "not reconciled this run (a failed read would look like no finds). Check the task "
            "role's DynamoDB read access to the api-cache table",
            errors,
        )
        return None
    out: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        p = e.get("request_params") or {}
        if p.get("origin_entity_type") == "bibliography" and p.get("origin_entity_id"):
            out.setdefault(str(p["origin_entity_id"]), []).append(e)
    return out


_APPROVED_DETAILS = ("pdf_url", "imagecount", "ocr", "mediatype", "title", "year")


def finds_for(
    index: Optional[Dict[str, List[Dict[str, Any]]]], record: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """The stored finds of one record: those queued under its BibliographyID and under every id it
    absorbed when identical citations were combined (``merged_from``), so a decision on a find of
    an absorbed record applies to the combined one. None when the record has none."""
    if index is None:
        return None
    ids = [str(record.get("BibliographyID") or "")] + [
        str(i) for i in record.get("merged_from") or [] if isinstance(i, str)
    ]
    out = [e for i in ids if i for e in index.get(i, [])]
    return out or None


def approved_details(event: Dict[str, Any]) -> Dict[str, Any]:
    """The finder metadata of an approved find that may go on the record: known keys only, and a
    PDF link only when it is a clean URL (the event is data read back from the store).
    """
    from src.utils.url_hygiene import clean_url

    meta = (event.get("request_params") or {}).get("source_metadata")
    if not isinstance(meta, dict):
        return {}
    out = {k: meta[k] for k in _APPROVED_DETAILS if k in meta}
    if "pdf_url" in out and not clean_url(str(out["pdf_url"])):
        out.pop("pdf_url")
    return out


def approved_finds(finds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The finds a human approved (affirmed, or already submitted to Phase 0), with their URL as
    ``clean_url`` gives it (read back from the store: re-checked). The first is the primary copy:
    one a download can fetch (a NARA record known to hold no scans goes last), then the oldest.
    """
    from src.utils.url_hygiene import clean_url

    ok = []
    for e in finds:
        clean = clean_url(str(e.get("endpoint") or ""))
        if e.get("review_status") in (fp.AFFIRMED, fp.SUBMITTED) and clean:
            ok.append({**e, "endpoint": clean})
    return sorted(ok, key=lambda e: (_no_scans(e), str(e.get("queried_at") or "")))


def _no_scans(event: Dict[str, Any]) -> bool:
    download = (event.get("request_params") or {}).get("download")
    return isinstance(download, dict) and download.get("object_count") == 0


def still_waiting(finds: List[Dict[str, Any]]) -> bool:
    """Whether any find of the record still awaits a human decision."""
    return any(e.get("review_status") == fp.PENDING for e in finds)
