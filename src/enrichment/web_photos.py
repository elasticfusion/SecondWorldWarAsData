"""Photos for an entity found by an OpenSERP image search, each one confirmed by Grok vision
(owner 2026-11-23).

A photo is an image with (or without) a caption. For each image-search result (the image's URL and
the page it is on):

1. **Text pre-filter** - one batched Grok request per query (``_verify_results_batch``) on the
   result's title, page and image URL, so obvious junk is never downloaded or shown to vision.
2. **The photo** - downloaded SSRF-safe and size-capped (``images.fetch_image_bytes_with_reason``),
   its sha256 and perceptual hash taken. Bytes (or a perceptual hash) already on the record are a
   duplicate - the same photo from another site; bytes already judged for this entity are not
   judged again (a verdict cache keyed on (sha256, entity), 90 days).
3. **The page** - fetched SSRF-safe and kept without its active content (``find_pages.keep``); the
   caption is read from it (the image's ``figcaption``, ``alt`` or ``title``), else the result title.
4. **Grok vision** - is the image a relevant photo of the subject, and is it a period (1939-1945)
   photograph. Every kept photo is confirmed by vision; a Grok failure is never a "no": it raises
   ``VerifyFailed`` and the entity is left unsearched (asked again next run).
5. **Kept** - only a confirmed photo is stored (owner 2026-11-23: nothing is stored for a "no"):
   its bytes under their checksum (they wait in ``unscanned/`` for a clean ClamAV scan,
   ``find_pages.keep_photo``) and the page it is on, without its active content. Until then the
   page is held in memory only, for the caption.

Every decision is recorded permanently as a small JSON record (``_record_decision``) instead of
the data: a photo URL Grok vision said "no" or "unsure" to is never downloaded again for that
entity, and a URL that is not a usable image (not an image, too large, undecodable) is never
downloaded again for anyone. Relevance is per entity (a photo that is not Patton may be Bradley);
only entity-independent faults block a URL for everyone. ``retract`` takes a kept photo back off
a record the same way (``scripts/retract_photo.py``).

An accepted photo carries its full provenance: image URL, page URL and kept page, checksum,
perceptual hash, caption, the vision verdict and reason, the period flag, and the owner's
assumption that it is in the public domain (``copyright_status``).
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from src.grok_client import BatchModeCollecting
from src.utils.url_hygiene import scrub

logger = logging.getLogger(__name__)

CACHE_TYPE = "photo_relevance_vision"  # request-bound (request_identity.REQUEST_BOUND)
_VERDICT_SOURCE = "photo_vision_v1"
_DECISION_SOURCE = "photo_decision_v1"
# decisions are kept as long as the cache keeps anything: in effect, permanently
DECISION_DAYS = 3650
RETRACTED, UNUSABLE, INFECTED = "retracted", "unusable", "infected"
# a URL that is "not an image" for everyone may only be a hotlink or challenge page served to us:
# looked at again after a month (relevance decisions per entity stay)
UNUSABLE_DAYS = 30
# fetch failures that are about the URL itself, not this attempt. NOT "ssrf_blocked": the fetcher
# also gives it when DNS fails (a resolver hiccup must never block a URL for everyone)
_UNUSABLE_FETCH = frozenset({"not_an_image", "too_large"})
YES, NO, UNSURE = "yes", "no", "unsure"
_ANSWERS = (YES, NO, UNSURE)
COPYRIGHT_ASSUMED = (
    "assumed_public_domain"  # owner 2026-11-23: photos are assumed public domain
)
_PAGE_TIMEOUT_S = 20
_HEADERS = {"User-Agent": "SecondWorldWarAsData/1.0 (photo provenance)"}


@dataclass(frozen=True)
class Subject:
    """What the photos are for: the entity (``<type>#<ULID>``, the key of its verdicts), the text
    pre-filter's context, and how vision is told what counts."""

    entity_key: str
    text_context: str
    subject: str
    what_counts: str


@dataclass
class _Known:
    """What the record already holds (and what this search has decided), for duplicates."""

    urls: Set[str] = field(default_factory=set)
    shas: Set[str] = field(default_factory=set)
    phashes: Set[str] = field(default_factory=set)

    @classmethod
    def of(cls, images: List[Any]) -> "_Known":
        known = cls()
        for img in images or []:
            if isinstance(img, dict):
                known.add(img)
        return known

    def add(self, img: Dict[str, Any]) -> None:
        for value, into in (
            (img.get("url"), self.urls),
            (img.get("sha256"), self.shas),
            (img.get("phash"), self.phashes),
        ):
            if isinstance(value, str) and value:
                into.add(value)


def _metric(key: str, n: int = 1) -> None:
    from src.enrichment import openserp_enrichment as oe

    oe._metric(key, n)


def find_photos(
    results: List[Dict[str, Any]],
    subject: Subject,
    grok_client: Any,
    existing: List[Any],
    wanted: int,
) -> List[Dict[str, Any]]:
    """Up to ``wanted`` new photos of ``subject`` from one image query's ``results``, each
    confirmed by Grok vision, as image entries for the record (``existing``: its images, for
    duplicates). Raises ``VerifyFailed`` (no usable Grok answer) and ``BatchModeCollecting``.
    """
    if wanted <= 0 or not grok_client:
        return []
    known = _Known.of(existing)
    fresh = _fresh(results, known, subject)
    if not fresh:
        return []
    photos: List[Dict[str, Any]] = []
    for r in _prefilter(fresh, subject, grok_client):
        if len(photos) >= wanted:
            break
        photo = _check(r, subject, grok_client, known)
        if photo is not None:
            photos.append(photo)
            known.add(photo)
    return photos


def _fresh(
    results: List[Dict[str, Any]], known: _Known, subject: Subject
) -> List[Dict[str, Any]]:
    """Results with a safe image URL that the record does not hold yet and that no earlier
    decision rules out (for this entity, or for everyone), once each."""
    from src.utils.find_queue import is_queueable

    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for r in results or []:
        url = r.get("url") or ""
        if not url or url in known.urls or url in seen or not is_queueable(url):
            continue
        seen.add(url)
        if _ruled_out(url, subject):
            _metric("photos_ruled_out")  # decided before: not downloaded again
            continue
        out.append(r)
    return out


def _prefilter(
    results: List[Dict[str, Any]], subject: Subject, grok_client: Any
) -> List[Dict[str, Any]]:
    """The results one batched text check calls plausible (title, page and image URL)."""
    from src.enrichment import openserp_enrichment as oe

    rows = [
        {
            "url": r["url"],
            "title": r.get("title") or "",
            "description": f"image on the page {r.get('page_url') or 'unknown'}",
        }
        for r in results
    ]
    verdicts = oe._verify_results_batch(
        subject.text_context, rows, grok_client, entity_key=subject.entity_key
    )
    passed = [r for r, ok in zip(results, verdicts) if ok]
    _metric("photos_prefiltered_out", len(results) - len(passed))
    return passed


def _check(
    r: Dict[str, Any], subject: Subject, grok_client: Any, known: _Known
) -> Optional[Dict[str, Any]]:
    """One plausible result: the photo entry when vision confirms it, else None."""
    from src.extraction.equipment import _compute_image_hash
    from src.extraction.images import fetch_image_bytes_with_reason
    from src.utils.find_pages import content_sha256

    body, ext, why = fetch_image_bytes_with_reason(r["url"])
    if body is None:
        _metric("photos_fetch_failed")
        logger.debug("photo not fetched (%s): %s", why, scrub(r["url"])[:120])
        if (
            why in _UNUSABLE_FETCH
        ):  # about the URL itself: never fetched again, for anyone
            _record_decision(r, None, {"verdict": UNUSABLE, "reason": why})
        return None
    sha = content_sha256(body)
    phash = _compute_image_hash(body)
    if sha in known.shas or (phash and phash in known.phashes):
        _metric("photos_duplicate")  # the same photo, found on another site
        return None
    known.shas.add(sha)  # decided for this entity in this search, whatever the verdict
    verdict = _cached_verdict(sha, subject)
    if verdict is not None and verdict["relevant"] != YES:
        _rejected(r, subject, sha, verdict)  # the same bytes, judged before
        return None
    page = _page(r)
    if verdict is None:
        verdict = _ask_and_keep(sha, body, r, page, subject, grok_client)
    if verdict["relevant"] != YES:
        _rejected(r, subject, sha, verdict)
        return None
    _record_decision(
        r, subject, {"verdict": YES, "sha256": sha, "reason": verdict["reason"]}
    )
    return _accepted(r, body, ext, phash, page, verdict)


def _rejected(
    r: Dict[str, Any], subject: Subject, sha: str, verdict: Dict[str, Any]
) -> None:
    """A "no" (or "unsure"): nothing is stored but the decision, so the URL is never fetched
    again for this entity - or for anyone, when the bytes are not a usable image."""
    _metric("photos_rejected")
    decision = {
        "verdict": verdict["relevant"],
        "sha256": sha,
        "reason": verdict["reason"],
    }
    _record_decision(r, subject, decision)
    if verdict.get("unusable"):
        _record_decision(r, None, {**decision, "verdict": UNUSABLE})


# --- decisions: small permanent JSON records instead of the data ---------------------------


def _decision_key(url: str, entity_key: str) -> str:
    """``entity_key`` "" is a decision for every entity (an unusable URL)."""
    return json.dumps(["url", url, entity_key])


def _record_decision(
    r: Dict[str, Any], subject: Optional[Subject], decision: Dict[str, Any]
) -> bool:
    """Keep what was decided about ``r``'s image URL (provenance: the page, when, why). Returns
    False when it could not be kept (said)."""
    from src.utils.search_cache import cache_result

    entity_key = subject.entity_key if subject else ""
    record = {
        **decision,
        "url": r.get("url"),
        "page_url": r.get("page_url") or None,
        "entity": entity_key or None,
        "decided_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    try:
        cache_result(
            _DECISION_SOURCE,
            _decision_key(str(r.get("url") or ""), entity_key),
            json.dumps(record),
            positive_days=(
                UNUSABLE_DAYS if record["verdict"] == UNUSABLE else DECISION_DAYS
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001 - the photo is still judged; said, not fatal
        logger.warning(
            "OBSERV web_photos.decision_not_recorded err=%s status=warn - the URL may be fetched "
            "again later. Check the task role's access to the api-cache table",
            type(e).__name__,
        )
        return False


def decision_for(url: str, entity_key: str = "") -> Optional[Dict[str, Any]]:
    """The recorded decision on ``url`` for ``entity_key`` ("" = for every entity), or None."""
    from src.utils.search_cache import get_cached

    raw = get_cached(_DECISION_SOURCE, _decision_key(url, entity_key))
    try:
        record = json.loads(raw) if raw else None
    except ValueError:
        return None
    return record if isinstance(record, dict) and record.get("verdict") else None


def _ruled_out(url: str, subject: Subject) -> bool:
    """A URL that is not a usable image (for anyone), or one this entity's photos rule out (a
    "no", an "unsure", a retraction). A "yes" rules nothing out."""
    for entity_key in ("", subject.entity_key):
        decided = decision_for(url, entity_key)
        if decided and decided["verdict"] != YES:
            return True
    return False


_S3: Dict[str, Any] = {}  # one S3 client per process (made under the lock)
_S3_LOCK = threading.Lock()


def _s3_and_bucket() -> "tuple[Any, str]":
    """(S3 client, data bucket), or (None, "") with no bucket (local runs)."""
    import os

    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        return None, ""
    with _S3_LOCK:
        if "client" not in _S3:
            import boto3

            region = os.environ.get("AWS_REGION", "us-east-1")
            _S3["client"] = boto3.client("s3", region_name=region)
        return _S3["client"], bucket


def drop_infected(images: List[Any]) -> int:
    """Remove the photos whose file ClamAV found infected (the drain deleted it,
    ``av_scan.was_infected``) from a record's ``images`` (in place), and rule out each URL for
    EVERYONE, permanently. Returns the number removed. No bucket (local runs): nothing to check.
    """
    from src.ingestion.av_scan import was_infected

    s3, bucket = _s3_and_bucket()
    if s3 is None:
        return 0
    bad = [
        i
        for i in images
        if isinstance(i, dict)
        and i.get("local_path")
        and was_infected(s3, bucket, str(i["local_path"]))
    ]
    for img in bad:
        images.remove(img)
        if img.get("url"):
            r = {"url": img["url"], "page_url": img.get("source_page_url")}
            _record_decision(
                r, None, {"verdict": INFECTED, "reason": "ClamAV: infected"}
            )
    if bad:
        _metric("photos_infected_dropped", len(bad))
        logger.warning(
            "OBSERV web_photos.infected_dropped count=%d status=warn - photos ClamAV found "
            "infected were removed from their record and their URLs ruled out",
            len(bad),
        )
    return len(bad)


def retract(images: List[Any], url: str, subject: Subject, reason: str) -> bool:
    """Take a kept photo back off a record (a person, or a later check, says it is not the
    subject): ``take_off`` removes its entry, ``record_retraction`` rules out its URL AND its bytes
    for this entity (a copy elsewhere included). Returns True when an entry was removed. The stored
    bytes stay (content-addressed: they may back another record's photo)."""
    gone = take_off(images, url)
    record_retraction(gone, url, subject, reason)
    return bool(gone)


def take_off(images: List[Any], url: str) -> List[Dict[str, Any]]:
    """Remove the entries for ``url`` from ``images`` (in place); returns them."""
    gone = [i for i in images if isinstance(i, dict) and i.get("url") == url]
    images[:] = [i for i in images if i not in gone]
    return gone


def record_retraction(
    gone: List[Dict[str, Any]], url: str, subject: Subject, reason: str
) -> bool:
    """Rule out ``url`` (and the bytes of each removed entry) for this entity. Returns False when
    a decision could not be recorded (said): the caller must not report success."""
    why = scrub(reason)[:300] or "retracted"
    ok = all(_retract_one(img, url, subject, why) for img in gone or [{"url": url}])
    if gone:
        _metric("photos_retracted", len(gone))
        logger.info(
            "OBSERV web_photos.retracted entity=%s count=%d recorded=%s",
            subject.entity_key,
            len(gone),
            ok,
        )
    return ok


def _retract_one(img: Dict[str, Any], url: str, subject: Subject, why: str) -> bool:
    from src.utils.search_cache import cache_result

    r = {"url": url, "page_url": img.get("source_page_url")}
    ok = _record_decision(r, subject, {"verdict": RETRACTED, "reason": why})
    if not img.get("sha256"):
        return ok
    try:  # the same bytes elsewhere are a "no" for this entity too
        cache_result(
            _VERDICT_SOURCE,
            _verdict_key(img["sha256"], subject),
            json.dumps({"relevant": NO, "period_photo": UNSURE, "reason": why}),
            positive_days=DECISION_DAYS,
        )
        return ok
    except Exception as e:  # noqa: BLE001 - said; the caller reports it
        logger.warning(
            "OBSERV web_photos.decision_not_recorded err=%s status=warn - a copy of the photo "
            "may be taken again. Check access to the api-cache table",
            type(e).__name__,
        )
        return False


def _verdict_key(sha: str, subject: Subject) -> str:
    return json.dumps([sha, subject.entity_key])


def _cached_verdict(sha: str, subject: Subject) -> Optional[Dict[str, Any]]:
    """The vision verdict on these bytes for this entity, from an earlier search."""
    from src.utils.search_cache import get_cached

    raw = get_cached(_VERDICT_SOURCE, _verdict_key(sha, subject))
    try:
        verdict = _parse(json.loads(raw)) if raw else None
    except ValueError:
        verdict = None
    if verdict is not None:  # validated like a live answer: a bad entry is asked again
        _metric("photos_verdict_cached")
    return verdict


def _ask_and_keep(
    sha: str,
    body: bytes,
    r: Dict[str, Any],
    page: Dict[str, Any],
    subject: Subject,
    grok_client: Any,
) -> Dict[str, Any]:
    """Ask vision and cache the answer for (bytes, entity). No usable answer: VerifyFailed."""
    from src.enrichment import openserp_enrichment as oe
    from src.utils.search_cache import cache_result

    verdict = _ask(body, page, subject, grok_client)
    if verdict is None:
        raise oe.VerifyFailed(f"{CACHE_TYPE}: no usable answer")
    cache_result(
        _VERDICT_SOURCE,
        _verdict_key(sha, subject),
        json.dumps(verdict),
        positive_days=DECISION_DAYS,
    )
    return verdict


def _ask(
    body: bytes, page: Dict[str, Any], subject: Subject, grok_client: Any
) -> Optional[Dict[str, Any]]:
    """Grok vision's verdict on one photo, or None when it gave no usable answer. An image that
    cannot be looked at (not an image, too large, a decompression bomb) is a decided "no".
    """
    from src.extraction.equipment import _prepare_image_for_vision
    from src.utils.prompt_loader import render_prompt

    prepared, problem = _prepare_image_for_vision(body)
    if prepared is None:  # about the bytes, not the entity: unusable for everyone
        return {
            "relevant": NO,
            "period_photo": UNSURE,
            "reason": scrub(problem)[:200],
            "unusable": True,
        }
    prompt = render_prompt(
        "photo_relevance_vision",
        subject=scrub(subject.subject)[:200],
        what_counts=scrub(subject.what_counts)[:300],
        caption=scrub(page.get("caption") or "")[:300],
        page_title=scrub(page.get("page_title") or "")[:200],
    )
    _metric("photos_vision_asked")
    try:
        answer = grok_client.extract_json_with_image_base64(
            prompt=prompt,
            image_base64=base64.b64encode(prepared).decode(),
            cache_type=CACHE_TYPE,
            temperature=0.0,
        )
    except BatchModeCollecting:
        raise  # queued for the batch: not an answer, not a failure
    except Exception as e:  # noqa: BLE001 - no answer: never a "no"
        logger.warning(
            "OBSERV web_photos.vision_failed err=%s status=warn", type(e).__name__
        )
        return None
    return _parse(answer)


def _parse(answer: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(answer, dict) or answer.get("relevant") not in _ANSWERS:
        return None
    period = answer.get("period_photo")
    verdict = {
        "relevant": answer["relevant"],
        "period_photo": period if period in _ANSWERS else UNSURE,
        "reason": scrub(str(answer.get("reason") or ""))[:300],
    }
    if (
        answer.get("unusable") is True
    ):  # kept on a cached verdict: a copy elsewhere is unusable too
        verdict["unusable"] = True
    return verdict


# --- the page the photo is on -----------------------------------------------------------------


def _page(r: Dict[str, Any]) -> Dict[str, Any]:
    """The page the photo is on, read for the caption: ``caption``, ``page_title``, its HTML
    (held in memory: kept only if the photo is accepted, ``_accepted``) or the fields of the copy
    kept before. A page that cannot be fetched leaves the result's title as the caption.
    """
    page_url = r.get("page_url") or ""
    html, kept = _page_html(page_url) if page_url else (None, {})
    if html is None:
        from src.utils.find_queue import clean_for_log
        from src.utils.observe import observe_warn_counted

        _metric("photos_page_not_kept")
        observe_warn_counted(
            "web_photos",
            "page_not_kept",
            key=("page",),
            every=25,
            note="the page a photo is on could not be fetched: the photo is judged on the search "
            "result's title and kept without its page. Many of these: check the OpenSERP image "
            "results' page URLs and the fetch logs",
            page=clean_for_log(page_url),
        )
        return {"caption": r.get("title") or "", "page_title": "", "page_kept": False}
    caption, title = caption_for(html, r["url"], page_url)
    page = {**kept, "caption": caption or r.get("title") or "", "page_title": title}
    if not kept.get("page_kept"):
        page["html"] = html  # not stored yet: only for a photo that is accepted
    return page


def _page_html(page_url: str) -> "tuple[Optional[str], Dict[str, Any]]":
    """``(the page's HTML, its kept-page fields)``: the copy kept before (no fetch), else a fresh
    SSRF-safe fetch (NOT stored here). ``(None, {})`` when the page cannot be had."""
    from src.utils import find_pages
    from src.utils.find_queue import is_queueable
    from src.utils.url_hygiene import fetch_text_safe

    if not is_queueable(page_url):
        return None, {}
    found = find_pages.lookup(page_url)
    if found["page_kept"]:
        try:
            body = find_pages.load(found)
            if body is not None:
                return body.decode("utf-8", errors="replace"), found
        except find_pages.PageUnusable:
            pass  # fetched again below
    html, status = fetch_text_safe(page_url, timeout=_PAGE_TIMEOUT_S, headers=_HEADERS)
    if status != 200 or not html:
        return None, {}
    return html, {}


def caption_for(html: str, image_url: str, page_url: str) -> "tuple[str, str]":
    """``(caption, page title)``: the ``figcaption`` of the figure holding the image, else its
    ``alt`` or ``title``; the image is matched by its resolved URL, else by its file name.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    title = _clean(soup.title.get_text() if soup.title else "")[:200]
    img = _matching_img(soup, image_url, page_url)
    if img is None:
        return "", title
    figure = img.find_parent("figure")
    figcaption = figure.find("figcaption") if figure else None
    for text in (
        figcaption.get_text(" ") if figcaption else "",
        img.get("alt"),
        img.get("title"),
    ):
        if _clean(text):
            return _clean(text)[:300], title
    return "", title


def _matching_img(soup: Any, image_url: str, page_url: str) -> Any:
    name = urlparse(image_url).path.rsplit("/", 1)[-1]
    by_name = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        resolved = urljoin(page_url, str(src))
        if resolved == image_url:
            return img
        if by_name is None and name and urlparse(resolved).path.endswith("/" + name):
            by_name = img
    return by_name


def _clean(text: Any) -> str:
    return " ".join(scrub(str(text or "")).split())


def _accepted(
    r: Dict[str, Any],
    body: bytes,
    ext: Optional[str],
    phash: Optional[str],
    page: Dict[str, Any],
    verdict: Dict[str, Any],
) -> Dict[str, Any]:
    """The image entry for a confirmed photo, once its bytes are kept for the AV scan. Raises
    ``FindNotStored`` when they could not be kept."""
    from src.utils import find_pages

    stored = find_pages.keep_photo(body, ext or "")
    if not stored["photo_kept"]:
        from src.utils.find_queue import FindNotStored, clean_for_log

        _metric("photos_not_kept")  # find_pages logged the error
        # a confirmed photo that could not be stored: the entity is left unsearched (its verdict
        # is cached, so the next run stores it without asking Grok again)
        raise FindNotStored(clean_for_log(r["url"]))
    if page.get("html"):  # the page is stored only now, for a photo that is kept
        page.update(find_pages.keep(r.get("page_url") or "", page.pop("html")))
        if not page.get("page_kept"):
            _metric("photos_page_not_kept")  # find_pages said why (too large / complex)
    _metric("photos_accepted")
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {
        "url": r["url"],
        "source": "openserp",
        "candidate_source": "openserp",
        "caption": page.get("caption") or None,
        "source_page_url": r.get("page_url") or None,
        "page_sha256": page.get("page_sha256"),
        "sha256": stored["sha256"],
        "phash": phash,
        "local_path": stored["local_path"],
        "vision_check": "verified",
        "vision_verified": True,
        "vision_reason": verdict["reason"],
        "vision_checked_at": now,
        "period_photo": verdict["period_photo"],
        "copyright_status": COPYRIGHT_ASSUMED,
        "candidate_added_at": now,
    }
