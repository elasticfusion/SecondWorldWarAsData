"""The page a queued find was judged on, kept so an approved find gives Phase 0 the page itself (not
a stub with the URL and summary).

A page is stored once under its checksum (``pages/<sha256>.html``: write-once, so what a reviewer
approved is what Phase 0 gets) with a pointer per URL (``urls/<sha256 of the clean URL>.json``) that
says which page the URL gave, when, and whether it came from the live site or the Internet Archive.
A later entity whose search returns the same URL reuses the summary without a fetch, and the pointer
gives it the same page.

Storage: S3 under ``find-pages/`` when ``S3_BUCKET`` is set (the Phase 3 task writes, the URL-review
Lambda reads; outside ``output/``, so no sync or trigger sees it), else ``output/_find_pages/``.

Every page is kept without its active content (``html_safety``: scripts, embeds, event handlers,
``javascript:`` URLs - owner 2026-11-23), and the checksum is of what is kept.

Photos (``keep_photo``) are kept under their checksum in the AV staging area
(``unscanned/output/photos/files/``): the ClamAV drain after Phase 3 promotes a clean one to
``output/photos/files/`` and quarantines an infected one, so no photo reaches production unscanned.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

S3_PREFIX = "find-pages"
_LOCAL_DIR = Path("output/_find_pages")
PHOTO_PATH = "output/photos/files"  # where a scanned-clean photo lives
PHOTO_STAGING_PREFIX = (
    "unscanned/" + PHOTO_PATH
)  # av_scan.UNSCANNED_PREFIX + its production path
_PHOTO_LOCAL_DIR = Path(PHOTO_PATH)
_EXT_RE = re.compile(r"[a-z0-9]{1,5}")
_WRITE_ATTEMPTS = 3
_RETRY_BASE_S = 0.5
_SHA_RE = re.compile(
    r"sha256:([0-9a-f]{64})"
)  # used with fullmatch (no trailing newline)
_MAX_BYTES = (
    25 * 1024 * 1024
)  # the most a kept page may be (the fetchers cap well below this)
_STORES: Dict[str, Any] = (
    {}
)  # bucket -> its S3Storage (one boto3 client, made under the lock)
_STORES_LOCK = threading.Lock()
# the fields a find carries about its page (a find's request_params; the pointer holds the same)
PAGE_FIELDS = (
    "page_sha256",
    "page_bytes",
    "page_fetched_at",
    "page_retrieved_from",
    "page_archived_url",
    "page_wayback_capture_timestamp",
)


def content_sha256(data: bytes) -> str:
    """``sha256:<hex>`` of the bytes (the digest format of the content-hash dedup)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _bucket() -> str:
    return os.environ.get("S3_BUCKET", "").strip()


def _storage(prefix: str = S3_PREFIX, local_dir: Optional[Path] = None) -> Any:
    from src.utils.storage import LocalStorage, S3Storage

    bucket = _bucket()
    if not bucket:
        return LocalStorage(_LOCAL_DIR if local_dir is None else local_dir)
    with (
        _STORES_LOCK
    ):  # creating boto3 clients from several threads at once is not safe
        key = f"{bucket}|{prefix}"
        if key not in _STORES:
            region = os.environ.get("AWS_REGION", "us-east-1")
            _STORES[key] = S3Storage(bucket=bucket, prefix=prefix, region=region)
        return _STORES[key]


def _page_path(sha: str) -> Optional[str]:
    m = _SHA_RE.fullmatch(sha or "")
    return f"pages/{m.group(1)}.html" if m else None


def _url_path(url: str) -> Optional[str]:
    from src.utils.url_hygiene import clean_url

    clean = clean_url(url)
    if not clean:
        return None
    return f"urls/{hashlib.sha256(clean.encode('utf-8')).hexdigest()}.json"


def _with_retry(what: str, write) -> bool:
    err = ""
    for attempt in range(1, _WRITE_ATTEMPTS + 1):
        try:
            write()
            return True
        except Exception as e:  # noqa: BLE001 - retried, then reported
            err = type(e).__name__
            if attempt < _WRITE_ATTEMPTS:
                time.sleep(_RETRY_BASE_S * attempt)
    logger.error(
        "OBSERV find_pages.write_failed what=%s err=%s attempts=%d status=error - not kept (a "
        "find's page: its approval gives Phase 0 only the URL and summary; a photo: it is not "
        "attached). Check the task role's s3:PutObject on find-pages/ and unscanned/ in the data "
        "bucket",
        what,
        err,
        _WRITE_ATTEMPTS,
    )
    return False


def _page_fields(body: bytes, prov: Dict[str, Any]) -> Dict[str, Any]:
    archived = prov.get("archived_url")
    return {
        "page_sha256": content_sha256(body),
        "page_bytes": len(body),
        "page_fetched_at": int(time.time()),
        "page_retrieved_from": "wayback" if archived else "live",
        "page_archived_url": archived or None,
        "page_wayback_capture_timestamp": prov.get("wayback_capture_timestamp") or None,
    }


def keep(
    url: str, html: str, provenance: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Store the page ``url`` gave (``html``, live or archived) and point the URL at it. Returns the
    page fields for the find (``PAGE_FIELDS``), or ``{"page_kept": False}`` when it could not be
    stored (logged as an error; the find is still queued, and the reviewer sees no page).
    """
    from src.utils.html_safety import PageTooComplex, strip_active_content

    if not (html or "").strip():
        return {"page_kept": False}
    try:
        safe = strip_active_content(html)
    except PageTooComplex as e:
        from src.utils.find_queue import clean_for_log
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "find_pages",
            "page_too_complex",
            key=("page",),
            every=25,
            note="a page too large, or too slow to strip within the budget, is not kept (its "
            "find is queued without it). Many of these: check html_safety's limits against the "
            "pages",
            url=clean_for_log(url),
            why=str(e)[:200],
        )
        return {"page_kept": False}
    body = safe.encode("utf-8")
    url_path = _url_path(url)
    if not body or not url_path:
        return {"page_kept": False}
    if len(body) > _MAX_BYTES:
        logger.warning(
            "OBSERV find_pages.oversized bytes=%d cap=%d status=warn - the page is not kept; its "
            "find is queued without it. Raise find_pages._MAX_BYTES if such pages are wanted",
            len(body),
            _MAX_BYTES,
        )
        return {"page_kept": False}
    fields = _page_fields(body, provenance or {})
    sha = fields["page_sha256"]
    store = _storage()
    page_path = _page_path(sha)
    if not _with_retry(
        "page", lambda: store.exists(page_path) or store.write_bytes(page_path, body)
    ):
        return {"page_kept": False}
    # the page is stored, so this find has it even if the pointer fails (a later cache hit then
    # has none, and says so)
    _with_retry("pointer", lambda: store.write_json(url_path, {"url": url, **fields}))
    logger.info(
        "OBSERV find_pages.kept bytes=%d fetched_bytes=%d source=%s %s",
        len(body),
        len(html.encode("utf-8", errors="replace")),
        fields["page_retrieved_from"],
        sha[:20],
    )
    return {**fields, "page_kept": True}


def lookup(url: str) -> Dict[str, Any]:
    """The page fields of the page ``url`` gave when it was last kept, or ``{"page_kept":
    False}`` (quietly: the caller decides whether that is worth saying)."""
    url_path = _url_path(url)
    try:
        store = _storage()
        if url_path and store.exists(url_path):
            pointer = store.read_json(url_path)
            fields = {k: pointer.get(k) for k in PAGE_FIELDS}
            if _page_path(str(fields.get("page_sha256") or "")):
                return {**fields, "page_kept": True}
    except Exception as e:  # noqa: BLE001 - no page: the caller says so
        logger.warning("OBSERV find_pages.pointer_unreadable err=%s", type(e).__name__)
    return {"page_kept": False}


def keep_photo(body: bytes, ext: str) -> Dict[str, Any]:
    """Store a photo under its checksum in the AV staging area (write-once). Returns ``sha256``
    and ``local_path`` (the production path a clean scan promotes it to), or ``{"photo_kept":
    False}`` when it could not be stored (logged as an error)."""
    if not body or len(body) > _MAX_BYTES:
        return {"photo_kept": False}
    sha = content_sha256(body)
    ext = ext.lower().lstrip(".") if isinstance(ext, str) else ""
    name = f"{sha[7:]}.{ext if _EXT_RE.fullmatch(ext) else 'img'}"
    store = _storage(PHOTO_STAGING_PREFIX, _PHOTO_LOCAL_DIR)
    if not _with_retry(
        "photo", lambda: store.exists(name) or store.write_bytes(name, body)
    ):
        return {"photo_kept": False}
    return {"sha256": sha, "local_path": f"{PHOTO_PATH}/{name}", "photo_kept": True}


def kept(url: str) -> Dict[str, Any]:
    """The page fields of the page ``url`` gave when it was last fetched, or ``{"page_kept":
    False}`` when none was kept (a summary cached before pages were kept, or a failed write).
    """
    found = lookup(url)
    if found["page_kept"]:
        return found
    from src.utils.find_queue import clean_for_log

    logger.warning(
        "OBSERV find_pages.not_kept url=%s status=warn - a cached page summary has no kept page "
        "(cached "
        "before pages were kept, or its write failed): the find is queued without the page, and its "
        "approval would give Phase 0 only the URL and summary. Reject it if the page matters; the "
        "URL is fetched again after its summary expires",
        clean_for_log(url),
    )
    return {"page_kept": False}


class PageUnusable(Exception):
    """A find names a kept page that is missing or is not the page it names (never submitted)."""


def load(fields: Dict[str, Any]) -> Optional[bytes]:
    """The kept page of a find (its ``page_sha256``), checked against the checksum. None when the
    find names no page (it was queued before pages were kept, or its write failed). Raises
    ``PageUnusable`` when the named page is missing or its bytes do not match."""
    sha = fields.get("page_sha256")
    if not sha:
        return None
    page_path = _page_path(str(sha))
    if page_path is None:
        raise PageUnusable("the find names a page with a malformed checksum")
    try:
        body = _storage().read_bytes(page_path)
    except (
        Exception
    ) as e:  # noqa: BLE001 - a missing or unreadable page is not submitted
        raise PageUnusable(f"the page could not be read ({type(e).__name__})") from e
    if content_sha256(body) != sha:
        raise PageUnusable("the page's bytes do not match its checksum")
    return body
