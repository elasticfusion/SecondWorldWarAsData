"""Acquisition fetchers for each resolved source (NARA / archive.org / OpenSERP / future).

Each fetcher inspects a RESOLVED bibliography entry and returns an
:class:`~src.enrichment.source_acquisition.AcquisitionTarget` describing the bytes to
download (format, page/object count, direct object URLs), or ``None`` if it can't. They are
registered into the pluggable registry so the acquisition pass stays source-agnostic.

Network I/O here is only the minimal metadata lookup needed to BUILD the target (e.g. the
NARA by-naId re-query for the digital-object list); the bulk download is done by the
acquisition pass honouring the review-gate verdict.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from src.enrichment.source_acquisition import AcquisitionTarget, register_fetcher

logger = logging.getLogger(__name__)

_NAID_RE = re.compile(r"/id/(\d+)")


def _http_only(urls: List[str]) -> List[str]:
    return [
        u
        for u in urls
        if isinstance(u, str) and u.lower().startswith(("http://", "https://"))
    ]


# --- NARA ----------------------------------------------------------------------------------
def _nara_fetcher(
    entry: Dict[str, Any], config: Dict[str, Any]
) -> Optional[AcquisitionTarget]:
    """Build the download target for a resolved NARA record by re-querying its naId for the
    digitalObjects list. The resolved entry stores only the /id/<naId> catalog URL, so we
    look up the objects (page scans) + their count here."""
    urls = entry.get("resource_urls") or []
    naid = ""
    for u in urls:
        m = _NAID_RE.search(str(u))
        if m:
            naid = m.group(1)
            break
    if not naid:
        return None
    api_key = config.get("nara_api_key")
    if not api_key:
        logger.debug("NARA fetcher: no api key -> cannot build target")
        return None
    try:
        from src.utils.http_pool import get_session

        session = get_session()
        resp = session.get(
            f"https://catalog.archives.gov/api/v2/records/{naid}",
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            resp = session.get(
                "https://catalog.archives.gov/api/v2/records/search",
                params={"naId": naid, "limit": "1"},
                headers={"x-api-key": api_key, "Accept": "application/json"},
                timeout=30,
            )
        if (
            resp.status_code != 200
            or "html" in (resp.headers.get("Content-Type") or "").lower()
        ):
            logger.info(
                "NARA fetcher: naId=%s lookup status=%s (skip)", naid, resp.status_code
            )
            return None
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.debug("NARA fetcher lookup failed for naId=%s: %s", naid, e)
        return None

    # Record may be at body.hits.hits[0]._source.record OR a single-record shape.
    record: Dict[str, Any] = {}
    hits = (payload.get("body", {}).get("hits", {}) or {}).get("hits", [])
    if hits:
        src = hits[0].get("_source", {}) or {}
        record = src.get("record", src) or {}
    else:
        record = payload.get("record", payload) or {}

    objs = _http_only(
        [
            o["objectUrl"]
            for o in (record.get("digitalObjects") or [])
            if isinstance(o, dict) and isinstance(o.get("objectUrl"), str)
        ]
    )
    # NOTE: the api_key is needed ONLY for the metadata lookup above. The objectUrl byte-
    # downloads are PUBLIC (verified: NARA media URLs return 200 with no key) — the download
    # pass must NOT send the key on the high-volume byte requests.
    # Infer the artifact format from the object extensions (pdf vs tif/jpg pages).
    fmt = (
        "pdf"
        if any(u.lower().endswith(".pdf") for u in objs)
        else ("tif" if objs else "image")
    )
    return AcquisitionTarget(
        fmt=fmt,
        pages=len(objs),
        object_urls=objs,
        detail=f"NARA naId {naid}, {len(objs)} digital objects",
    )


# --- archive.org ---------------------------------------------------------------------------
def _archive_org_fetcher(
    entry: Dict[str, Any], config: Dict[str, Any]
) -> Optional[AcquisitionTarget]:
    """archive.org items expose a clean download endpoint. The resolved URL is typically an
    item/details URL; we record it as a single acquirable PDF target (the pass resolves the
    concrete file via the item's /download/ listing)."""
    urls = _http_only(entry.get("resource_urls") or [])
    item = next((u for u in urls if "archive.org" in u.lower()), "")
    if not item:
        return None
    return AcquisitionTarget(
        fmt="pdf", pages=0, object_urls=[item], detail="archive.org item"
    )


# --- OpenSERP ------------------------------------------------------------------------------
def _openserp_fetcher(
    entry: Dict[str, Any], config: Dict[str, Any]
) -> Optional[AcquisitionTarget]:
    """OpenSERP yields arbitrary web URLs. Only acquire when the verified result is a DIRECT
    document (a .pdf/.epub/.doc/... URL), never an HTML landing page."""
    urls = _http_only(entry.get("resource_urls") or [])
    doc_exts = (".pdf", ".epub", ".doc", ".docx", ".txt", ".csv", ".rtf")
    direct = next((u for u in urls if u.lower().split("?")[0].endswith(doc_exts)), "")
    if not direct:
        return None
    ext = direct.lower().split("?")[0].rsplit(".", 1)[-1]
    return AcquisitionTarget(
        fmt=ext, pages=0, object_urls=[direct], detail="openserp direct document"
    )


def register_all() -> None:
    """Register every built-in fetcher. Call once at acquisition-pass startup."""
    register_fetcher("nara_catalog", _nara_fetcher)
    register_fetcher("archive_org", _archive_org_fetcher)
    register_fetcher("openserp_archive", _openserp_fetcher)
    register_fetcher("openserp", _openserp_fetcher)
