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
import json
import re
from typing import Any, Dict, List, Optional

from src.enrichment.source_acquisition import AcquisitionTarget, register_fetcher

logger = logging.getLogger(__name__)

_NAID_RE = re.compile(r"/id/(\d+)")
# location fields kept from a catalog reply (an external API: every value is checked and capped)
MAX_CONTAINERS = 20
CONTAINER_LEN, MEDIA_LEN, UNIT_LEN, EMAIL_LEN = 80, 40, 200, 80
_ASCII_INT = re.compile(r"[0-9]{1,6}")


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
    naid = _nara_naid_from_urls(urls)
    if not naid:
        return None
    api_key = config.get("nara_api_key")
    if not api_key:
        logger.debug("NARA fetcher: no api key -> cannot build target")
        return None
    looked_up = nara_digital_objects(naid, api_key)
    if looked_up is None:
        return None
    approved = _approved_manifest(entry, naid)
    if not approved:
        logger.warning(
            "OBSERV acquisition.nara_scans_unpinned naId=%s status=warn - no approved scan-list "
            "fingerprint (approved before 2026-11-22): the list is not checked against what a "
            "person saw. Compare the scans at https://catalog.archives.gov/id/%s with the citation "
            "before downloading (there is no re-approval step yet; TODO Group 3)",
            naid,
            naid,
        )
    elif approved != looked_up.get("manifest_sha256"):
        logger.error(
            "OBSERV acquisition.nara_scans_changed naId=%s status=error - NARA's scan list is not "
            "the one a person approved; nothing is downloaded. Review the record's scans again "
            "(https://catalog.archives.gov/id/%s)",
            naid,
            naid,
        )
        return None
    objs = looked_up["object_urls"]
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


def _approved_manifest(entry: Dict[str, Any], naid: str) -> str:
    """The scan-list fingerprint a person approved for this NARA record, if one was recorded."""
    for f in (entry.get("source_metadata") or {}).get("findings") or []:
        if isinstance(f, dict) and str(f.get("naId") or "") == naid:
            fp = f.get("manifest_sha256")
            if isinstance(fp, str) and re.fullmatch(r"[0-9a-f]{64}", fp):
                return fp
    return ""


def nara_digital_objects(naid: str, api_key: str) -> Optional[Dict[str, Any]]:
    """The downloadable page scans of one NARA record: ``{"title", "object_urls"}`` (every
    http(s) ``digitalObjects[].objectUrl``), or None when the record could not be read. One
    catalog API call. The /id/<naId> page is the citable resource; these are what a download
    fetches (a human starts it)."""
    payload = _fetch_nara_record(naid, api_key)
    if payload is None:
        return None
    record = _record_from_nara_payload(payload)
    if not _is_record_for(record, naid):
        # an answer with no record in it (a search fallback with no hit): unknown, NOT "no scans"
        logger.info(
            "NARA lookup: naId=%s returned no record (scan count unknown)", naid
        )
        return None
    level = str(record.get("levelOfDescription") or "").lower()
    scans = nara_scans(record)
    if scans["refused"]:
        logger.warning(
            "OBSERV nara.scan_urls_refused naId=%s refused=%d status=warn - scan URLs outside "
            "%s%s were dropped; if NARA moved its media, add the path to NARA_SCAN_PATHS",
            naid,
            scans["refused"],
            NARA_SCAN_HOST,
            "|".join(NARA_SCAN_PATHS),
        )
    return {
        "title": str(record.get("title") or ""),
        "object_urls": [s["url"] for s in scans["scans"]],
        **scans,
        # a series or record group holds its scans on its child items: its own list says nothing
        # about whether the cited records are online (only an item or file unit's list does)
        "level": level,
        "scans_known": level in ("item", "fileunit", "file unit", ""),
        # where the record sits (record group > series > file unit): sorts the NARA pull list
        "series_path": [
            _text(a["title"])
            for a in _ancestors(record)
            if isinstance(a.get("title"), str)
        ][:8],
        **nara_location(record, naid),
    }


def small_int(value: Any) -> Optional[int]:
    """An API value as a small non-negative int (ASCII digits, at most 6), else None: never
    raises, whatever the reply holds (a Unicode digit, a 5,000-digit string, a list)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < 10**6 else None
    text = str(value).strip() if isinstance(value, str) else ""
    return int(text) if _ASCII_INT.fullmatch(text) else None


def _ancestors(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The record's ancestors (dicts), broadest first (record group, then series)."""
    anc = [a for a in record.get("ancestors") or [] if isinstance(a, dict)]
    return sorted(anc, key=lambda a: -(small_int(a.get("distance")) or 0))


def _text(value: Any, limit: int = 200) -> str:
    """A text field of an API reply: a string, trimmed and capped; anything else is empty."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def _ancestor(record: Dict[str, Any], level: str) -> Dict[str, Any]:
    """The NEAREST ancestor at ``level`` (a record under nested series: the series it is in)."""
    found = [a for a in _ancestors(record) if a.get("levelOfDescription") == level]
    return found[-1] if found else {}


def _occurrences(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in record.get("physicalOccurrences") or [] if isinstance(o, dict)]


def _containers(record: Dict[str, Any], naid: str = "") -> List[Dict[str, str]]:
    """Each box or reel (``mediaOccurrences[].containerId``) with its media type and copy status,
    once each, in reply order, at most MAX_CONTAINERS (more is said)."""
    seen, out = set(), []
    for occ in _occurrences(record):
        for m in occ.get("mediaOccurrences") or []:
            if not (isinstance(m, dict) and isinstance(m.get("containerId"), str)):
                continue
            row = {
                "container": _text(m["containerId"], CONTAINER_LEN),
                "media": _text(m.get("specificMediaType"), MEDIA_LEN),
                "copy_status": _text(occ.get("copyStatus"), MEDIA_LEN),
            }
            ident = (row["container"], row["media"], row["copy_status"])
            if (
                row["container"] and ident not in seen
            ):  # a preservation and a reference copy
                seen.add(ident)  # of one reel are two rows
                out.append(row)
    if len(out) > MAX_CONTAINERS:
        logger.warning(
            "OBSERV nara.containers_capped naId=%s kept=%d of=%d status=warn - the pull list "
            "shows the first %d; open the catalog record for the rest",
            naid,
            MAX_CONTAINERS,
            len(out),
            MAX_CONTAINERS,
        )
    return out[:MAX_CONTAINERS]


def _reference_unit(record: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The reading room that serves the record: the one on the first occurrence that names one
    (a record held once per site has one; NARA lists the textual reference unit first).
    """
    for occ in _occurrences(record):
        for unit in occ.get("referenceUnits") or []:
            if isinstance(unit, dict) and isinstance(unit.get("name"), str):
                return {
                    "name": _text(unit["name"], UNIT_LEN),
                    "email": _text(unit.get("email"), EMAIL_LEN),
                }
    return None


def nara_location(record: Dict[str, Any], naid: str = "") -> Dict[str, Any]:
    """Where a NARA record is held and how to ask for it, from the catalog reply itself: the record
    group number and series (``ancestors``), each container (box or reel) with its media type and
    copy status, and the reference unit (reading room) that serves it. What a person needs on a
    pull slip; no extra request. Never raises; a file unit or item with no container is said.
    """
    series = _ancestor(record, "series")
    out = {
        "record_group_number": small_int(
            _ancestor(record, "recordGroup").get("recordGroupNumber")
        ),
        "series": (
            {
                "title": _text(series.get("title")),
                "naId": nara_id_digits(series.get("naId")),
            }
            if series
            else None
        ),
        "containers": _containers(record, naid),
        "reference_unit": _reference_unit(record),
    }
    level = str(record.get("levelOfDescription") or "").lower()
    held_on_site = bool(
        _occurrences(record)
    )  # an online-only (born-digital) record is not
    if level in ("item", "fileunit") and held_on_site and not out["containers"]:
        logger.warning(
            "OBSERV nara.no_container naId=%s level=%s status=warn - the catalog names no box or "
            "reel for it: a pull slip needs one; ask the reference unit or check the series",
            naid,
            level,
        )
    return out


def nara_id_digits(value: Any) -> Optional[str]:
    """A NARA id from a reply as digits (up to 15), else None."""
    text = (
        str(value)
        if isinstance(value, (int, str)) and not isinstance(value, bool)
        else ""
    )
    return text if re.fullmatch(r"[0-9]{1,15}", text) else None


def _is_record_for(record: Dict[str, Any], naid: str) -> bool:
    """Whether a parsed NARA answer is a catalog record (its own naId, or at least a title or a
    digitalObjects list), not an empty search shell."""
    if not isinstance(record, dict) or not record:
        return False
    own = str(record.get("naId") or "")
    if own:
        return own == str(naid)
    return bool(record.get("title") or record.get("digitalObjects"))


def _record_from_nara_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the record dict from a NARA payload — either the search shape
    (body.hits.hits[0]._source.record) or a single-record shape."""
    hits = (payload.get("body", {}).get("hits", {}) or {}).get("hits", [])
    if hits:
        src = hits[0].get("_source", {}) or {}
        return src.get("record", src) or {}
    return payload.get("record", payload) or {}


# where NARA serves page scans (seen live 2026-11-22: /medialive/; earlier /medialz/)
NARA_SCAN_HOST = "catalog.archives.gov"
NARA_SCAN_PATHS = ("/medialive/", "/medialz/")


def nara_scan_url_ok(url: Any) -> bool:
    """A scan URL a download may fetch: a clean https URL on NARA's own media paths."""
    from urllib.parse import urlparse

    from src.utils.url_hygiene import clean_url

    if not isinstance(url, str) or len(url) > 2048 or not url.startswith("https://"):
        return False
    if not clean_url(url):
        return False
    p = urlparse(url)
    return (
        _plain_scan_parts(p)
        and (p.hostname or "").lower() == NARA_SCAN_HOST
        and p.path.startswith(NARA_SCAN_PATHS)
    )


def _plain_scan_parts(p: Any) -> bool:
    """No dot segments, escapes, path parameters, query, fragment, other ports or credentials
    (clean_url has
    already refused a port that is not a number)."""
    # any escape is refused (a real name with %20 is dropped and counted, not decoded)
    odd_path = any(c in p.path for c in ("..", "%", ";", "\\"))
    extras = p.params or p.query or p.fragment or "@" in p.netloc
    return not (odd_path or extras or p.port not in (None, 443))


def nara_scans(record: Dict[str, Any]) -> Dict[str, Any]:
    """The page scans of a NARA record (``digitalObjects``) as a download would fetch them: each
    scan's URL, file name, type and size, in reply order; any URL off NARA's media paths is dropped
    and counted. ``manifest_sha256`` fingerprints the list (URLs + sizes), so a download can tell
    that it still fetches exactly what a person approved."""
    import hashlib

    scans, refused = [], 0
    for o in record.get("digitalObjects") or []:
        if not isinstance(o, dict):
            continue
        if not nara_scan_url_ok(o.get("objectUrl")):
            refused += 1
            continue
        scans.append(
            {
                "url": o["objectUrl"],
                "filename": _text(o.get("objectFilename"), 200),
                "type": _text(o.get("objectType"), 40),
                "size": small_int_bytes(o.get("objectFileSize")),
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps([[s["url"], s["size"]] for s in scans]).encode("utf-8")
    ).hexdigest()
    return {
        "scans": scans,
        "refused": refused,
        "total_bytes": sum(s["size"] or 0 for s in scans),
        "types": sorted({s["type"] for s in scans if s["type"]}),
        "manifest_sha256": fingerprint,
    }


def small_int_bytes(value: Any) -> Optional[int]:
    """A file size from the reply (ASCII digits or an int, up to 1 TB), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 10**12 else None
    text = value.strip() if isinstance(value, str) else ""
    return (
        int(text)
        if re.fullmatch(r"[0-9]{1,13}", text) and int(text) <= 10**12
        else None
    )


def _nara_object_urls(record: Dict[str, Any]) -> "list":
    """The checked scan URLs of a NARA record (``nara_scans``)."""
    return [s["url"] for s in nara_scans(record)["scans"]]


def _nara_naid_from_urls(urls: "list") -> str:
    """Extract the first naId found in the entry's resource_urls (the resolved entry stores only
    the /id/<naId> catalog URL)."""
    for u in urls:
        m = _NAID_RE.search(str(u))
        if m:
            return m.group(1)
    return ""


def _fetch_nara_record(naid: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Look up a NARA record by naId (direct records/{naId}, falling back to the search API).
    Returns the parsed JSON payload, or None on non-200 / HTML-shell / parse failure."""
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        headers = {"x-api-key": api_key, "Accept": "application/json"}
        resp = recorded_fetch(
            "nara",
            f"https://catalog.archives.gov/api/v2/records/{naid}",
            kind=TEXTUAL,
            method="GET",
            headers=headers,
            timeout=30,
            result_summary_fn=lambda r: f"naid={naid} status={r.status_code}",
        )
        if resp.status_code != 200:
            resp = recorded_fetch(
                "nara",
                "https://catalog.archives.gov/api/v2/records/search",
                kind=TEXTUAL,
                method="GET",
                params={"naId": naid, "limit": "1"},
                headers=headers,
                timeout=30,
                result_summary_fn=lambda r: f"naid-search={naid} status={r.status_code}",
            )
        if (
            resp.status_code != 200
            or "html" in (resp.headers.get("Content-Type") or "").lower()
        ):
            logger.info(
                "NARA fetcher: naId=%s lookup status=%s (skip)", naid, resp.status_code
            )
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.debug("NARA fetcher lookup failed for naId=%s: %s", naid, e)
        return None


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
