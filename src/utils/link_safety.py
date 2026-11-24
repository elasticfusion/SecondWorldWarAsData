"""Known-malicious links: found with Google Safe Browsing and eliminated (owner 2026-11-24).

Provider: the Safe Browsing Lookup API (v4), free and for NON-COMMERCIAL use only. Before the paid
tiers launch, switch ``_lookup`` to Google Web Risk (the commercial API; same lists) - the owner
decision is "use the free version; convert when the commercial components are in place".

What is checked (``flagged``, batched, up to 500 URLs per request): search results before any Grok
request is spent on them, every pinned GET (``url_hygiene._pinned_get_once``: ``safe_get``,
``url_fetch`` and each of its redirect hops, image downloads, every ``vet_url_for_fetch`` caller),
every find before a human sees it (``find_queue``), and every link and image source inside a page
we keep (``find_pages.keep`` -> ``strip_flagged_links``; relative and ``//host`` links resolved
against the page, at most ``_PAGE_MAX`` per page). The URL asked about keeps its query string
(Safe Browsing lists exact URLs); only the fragment is dropped.

What happens to a flagged URL: it is not fetched, not queued and not kept; inside a kept page its
``href`` / ``src`` is removed (the text stays, so the sentence still reads). The decision is a
record (``link_flagged_v1``, 10 years: URL, threat types, when, where it was seen; "Advisory
provided by Google") and one operator alert per URL, the first time it is flagged. A false positive
is cleared with ``scripts/allow_link.py`` (an operator allow record wins over a later flag).

Clean answers are kept ``link_safety.clean_days`` (30) so a URL is asked about at most once a
month. When the API cannot be reached the URL is NOT blocked (a counted warning says so): the other
layers (SSRF checks, active-content stripping, ClamAV) still apply, and nothing unchecked is cached.

Off until the API key exists: secret ``<env>-wwii-pipeline/safe-browsing-api-key`` (loaded into
``SAFE_BROWSING_API_KEY`` by ``ecs_entrypoint``); ``link_safety.enabled: false`` turns it off.
"""

from __future__ import annotations

import datetime as _dt
import functools
import ipaddress
import html as _html
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)


class LinkFlagged(requests.exceptions.InvalidURL):
    """A fetch of a URL known to be malicious was refused (not a network error: not retried)."""


KEY_ENV = "SAFE_BROWSING_API_KEY"
PROVIDER = "google-safe-browsing-v4"
ADVISORY = "Advisory provided by Google"
_ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
_THREAT_TYPES = (
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
)
_BATCH = 500  # the most URLs one request may carry
_TIMEOUT_S = 10
_CLEAN_SOURCE = "link_safety_clean_v1"
_FLAGGED_SOURCE = "link_flagged_v1"
_ALLOWED_SOURCE = "link_allowed_v1"
_RECORD_DAYS = 3650
DEFAULT_CLEAN_DAYS = 30
_CLEAN = "clean"
_PAGE_MAX = 5000  # the most links of one kept page that are checked (batches of 500)
_BREAKER_S = 60  # after an unanswered request, the API is not asked for this long
_breaker = {"until": 0.0}
_breaker_lock = threading.Lock()


@functools.lru_cache(maxsize=1)
def _config() -> Dict[str, Any]:
    """``link_safety`` from config.yaml, read once per process."""
    try:
        from src.utils.config import load_config

        return dict(load_config().get("link_safety") or {})
    except Exception:  # noqa: BLE001 - no config: the defaults
        return {}


def normalize(url: str) -> Optional[str]:
    """The URL as asked about and recorded: a safe http(s) URL (``clean_url`` must accept it),
    whitespace stripped, scheme lowercased, the host as a browser resolves it (``_host``),
    fragment dropped, query KEPT. None if it is not one."""
    from src.utils.url_hygiene import clean_url

    if not isinstance(url, str):
        return None
    raw = "".join(ch for ch in url.strip() if ch >= " " and ch != "\x7f")
    if not raw or clean_url(raw) is None:
        return None
    parts = urlsplit(raw)
    netloc = _host(parts)
    if not netloc:
        return None
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path or "/", parts.query, "")
    )


# characters that, once a host is percent-decoded, would be URL structure (or control bytes)
_HOST_STRUCTURE_RE = re.compile(r"[\x00-\x20/\\?#@%\[\]:]")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _ascii_host(raw: str) -> str:
    """The host's ASCII form ("" for one a browser would refuse). Raises ``UnicodeError`` for a
    host IDNA cannot map."""
    from urllib.parse import unquote

    if (
        ":" in raw
    ):  # an IPv6 literal (urlsplit strips its brackets): canonical, bracketed
        return f"[{ipaddress.IPv6Address(raw).compressed}]"
    decoded = unquote(raw)
    if _HOST_STRUCTURE_RE.search(decoded):
        return ""  # decodes into URL structure or control bytes
    host = decoded.encode("idna").decode("ascii").lower().rstrip(".")
    return _ipv4(host) or host


def _ipv4(host: str) -> str:
    """The dotted-decimal form of a host a browser reads as an IPv4 address (``16909060``,
    ``0x1.2.3.4``, ``01.2.3.4``), else ""."""
    import socket

    if not host or not re.fullmatch(r"[0-9a-fx.]+", host) or not host[-1:].isalnum():
        return ""
    if not any(ch.isdigit() for ch in host.split(".")[-1][:1]):
        return (
            ""  # a name whose last label starts with a letter (``cafe.ab``) is a name
        )
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return ""


def _host(parts: Any) -> str:
    """``host[:port]`` as a browser resolves it: percent-decoded, IDNA-mapped (fullwidth letters
    and the ideographic / fullwidth full stops become ASCII; ``bücher.de`` -> ``xn--bcher-kva.de``),
    lowercase, no trailing dot (also one that IDNA mapping produces), default port dropped. ""
    when it cannot be mapped, carries userinfo, or decodes into URL structure."""
    if parts.username is not None or parts.password is not None:
        return ""
    try:
        port = parts.port
        host = _ascii_host(parts.hostname or "")
    except (UnicodeError, ValueError):
        return ""
    if not host:
        return ""
    if port is None or _DEFAULT_PORTS.get(parts.scheme.lower()) == port:
        return host
    return f"{host}:{port}"


def _api_key() -> str:
    """The API key when the check is on, else "" (off: said once per process)."""
    if _config().get("enabled", True) is False:
        return ""
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "link_safety",
            "off_no_key",
            key=("off",),
            every=100000,
            note="known-malicious links are NOT being checked: no Safe Browsing API key. Create "
            "the secret <env>-wwii-pipeline/safe-browsing-api-key (a Google Cloud API key with "
            "the Safe Browsing API enabled), or set link_safety.enabled: false to silence this",
        )
    return key


def _clean_days() -> int:
    raw = _config().get("clean_days", DEFAULT_CLEAN_DAYS)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 30


def _cache_get(source: str, url: str) -> Optional[str]:
    from src.utils.search_cache import get_cached

    try:
        return get_cached(source, url)
    except Exception:  # noqa: BLE001 - unknown: asked again
        return None


def _cache_put(source: str, url: str, value: str, days: float) -> None:
    from src.utils.search_cache import cache_result

    try:
        cache_result(source, url, value, positive_days=days)
    except (
        Exception
    ) as e:  # noqa: BLE001 - said: the URL is asked about again next time
        logger.warning(
            "OBSERV link_safety.record_not_saved source=%s err=%s status=warn - the answer is "
            "not kept, so the URL is asked about again. Check the task role's access to the "
            "api-cache table",
            source,
            type(e).__name__,
        )


def flagged_record(url: str) -> Optional[Dict[str, Any]]:
    """The record of a URL found malicious (and not allowed by an operator since), or None."""
    clean = normalize(url)
    return _record_for(clean) if clean else None


def _record_for(clean: str) -> Optional[Dict[str, Any]]:
    """``flagged_record`` of an already normalized URL: the flag record first (most URLs have
    none: one read), the operator's allow record only when there is a flag."""
    raw = _cache_get(_FLAGGED_SOURCE, clean)
    try:
        record = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not (isinstance(record, dict) and record.get("threats")):
        return None
    return None if _cache_get(_ALLOWED_SOURCE, clean) else record


def _lookup(urls: List[str], key: str) -> Optional[Dict[str, List[str]]]:
    """Ask Safe Browsing about ``urls`` (at most ``_BATCH``). Returns url -> threat types for the
    matches, or None when there is no usable answer (said; the API is then left alone for
    ``_BREAKER_S``, so an outage does not add a timeout to every fetch)."""
    with _breaker_lock:
        if time.monotonic() < _breaker["until"]:
            return None
    body = {
        "client": {"clientId": "secondworldwarasdata", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": list(_THREAT_TYPES),
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls],
        },
    }
    answer = _post(body, key)
    if answer is None:
        return None
    return _matches(answer, urls)


def _post(body: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """The API's answer, or None (said)."""
    try:
        # the key goes in a header, never the URL: an exception message may carry the URL
        resp = requests.post(
            _ENDPOINT,
            json=body,
            headers={"X-Goog-Api-Key": key},
            timeout=_TIMEOUT_S,
        )
        answer = resp.json() if resp.status_code == 200 else None
    except Exception as e:  # noqa: BLE001 - no answer: not blocked, said
        _unavailable(type(e).__name__)
        return None
    if not isinstance(answer, dict):
        _unavailable(f"HTTP {resp.status_code}")
        return None
    return answer


def _matches(answer: Dict[str, Any], urls: List[str]) -> Dict[str, List[str]]:
    """url -> threat types, for the asked URLs the answer lists."""
    asked = set(urls)
    out: Dict[str, List[str]] = {}
    for m in answer.get("matches") or []:
        url = ((m or {}).get("threat") or {}).get("url")
        threat = (m or {}).get("threatType")
        if url in asked and isinstance(threat, str):
            out.setdefault(url, []).append(threat[:40])
    return out


def _unavailable(why: str) -> None:
    from src.utils.observe import observe_warn_counted

    with _breaker_lock:
        _breaker["until"] = time.monotonic() + _BREAKER_S
    observe_warn_counted(
        "link_safety",
        "unavailable",
        key=("api",),
        every=50,
        note="Safe Browsing gave no answer, so these URLs were NOT checked (not blocked, not "
        "cached: asked again next time). Check the API key (secret <env>-wwii-pipeline/"
        "safe-browsing-api-key), its quota in the Google Cloud console, and egress to "
        "safebrowsing.googleapis.com",
        why=why[:60],
    )


def flagged(urls: Iterable[str], seen_in: str = "") -> Dict[str, Dict[str, Any]]:
    """The URLs among ``urls`` known to be malicious (``_flagged``). Never raises: an
    unexpected error checks nothing (said, like an unanswered request)."""
    try:
        return _flagged(urls, seen_in)
    except Exception as e:  # noqa: BLE001 - the check must never stop a fetch or a step
        _unavailable(f"error {type(e).__name__}")
        return {}


def _flagged(urls: Iterable[str], seen_in: str = "") -> Dict[str, Dict[str, Any]]:
    """The URLs among ``urls`` known to be malicious: original URL -> its record. Cached answers
    first; the rest asked in batches. ``seen_in`` (where the URLs were found) goes on a new record.
    Empty when the check is off. Never raises."""
    from src.utils.find_queue import clean_for_log

    key = _api_key()
    if not key:
        return {}
    seen_in = clean_for_log(seen_in, 200)
    out, ask = _from_records(urls)
    todo = list(ask)
    for i in range(0, len(todo), _BATCH):
        _ask(todo[i : i + _BATCH], ask, key, seen_in, out)
    return out


def _from_records(
    urls: Iterable[str],
) -> "tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]":
    """What the records already say: the flagged URLs (original -> record), and the URLs to ask
    about (normalized -> the originals it stands for)."""
    out: Dict[str, Dict[str, Any]] = {}
    ask: Dict[str, List[str]] = {}
    for url in urls:
        clean = normalize(url)
        if not clean or _cache_get(_CLEAN_SOURCE, clean):  # most URLs: one lookup
            continue
        record = _record_for(clean)
        if record:
            out[url] = record
        else:
            ask.setdefault(clean, []).append(url)
    return out, ask


def _ask(
    batch: List[str],
    ask: Dict[str, List[str]],
    key: str,
    seen_in: str,
    out: Dict[str, Dict[str, Any]],
) -> None:
    """Ask about one batch; record the answer (flagged into ``out``, the rest kept clean)."""
    found = _lookup(batch, key)
    if found is None:
        return
    logger.info(
        "OBSERV link_safety.checked asked=%d flagged=%d seen_in=%s",
        len(batch),
        len(found),
        seen_in[:60] or "-",  # already cleaned for a log line
    )
    clean_days = _clean_days()
    for clean in batch:
        record = _flag(clean, found[clean], seen_in) if clean in found else {}
        if record:
            for url in ask[clean]:
                out[url] = record
        else:  # not listed, or allowed by an operator
            _cache_put(_CLEAN_SOURCE, clean, _CLEAN, clean_days)


def is_flagged(url: str, seen_in: str = "") -> bool:
    return bool(flagged([url], seen_in))


def _flag(clean: str, threats: List[str], seen_in: str) -> Dict[str, Any]:
    """Record a newly flagged URL (the first time it is seen: alerted). {} when an operator
    allowed it."""
    if _cache_get(_ALLOWED_SOURCE, clean):
        return {}
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    record = {
        "url": clean,
        "threats": sorted(set(threats)),
        "provider": PROVIDER,
        "advisory": ADVISORY,
        "flagged_at": now,
        "seen_in": [seen_in] if seen_in else [],
    }
    _cache_put(_FLAGGED_SOURCE, clean, json.dumps(record), _RECORD_DAYS)
    _alert(record)
    return record


def _alert(record: Dict[str, Any]) -> None:
    from src.utils.find_queue import clean_for_log
    from src.utils.observe import publish_alert

    # defanged: the alert's readers (email, Slack) would otherwise get a live link to it
    url = clean_for_log(defang(record["url"]), 200)
    line = (
        f"OBSERV link_safety.flagged url={url} threats={','.join(record['threats'])} "
        f"seen_in={clean_for_log(';'.join(record['seen_in']), 200) or '-'} status=warn - "
        "Google Safe Browsing lists this URL as possibly malicious: it is not fetched, not "
        "queued, and removed from kept pages. If it is a false positive, clear it with "
        "`python scripts/allow_link.py --url <url> --reason <why>`. "
        f"{ADVISORY} (https://developers.google.com/safe-browsing/v4/advisory)"
    )
    logger.warning(line)
    publish_alert("WWII Pipeline: possibly malicious link removed", line)


def defang(url: str) -> str:
    """``hxxps://host[.]tld/...``: readable, not clickable."""
    head, sep, rest = (url or "").partition("://")
    if not sep:
        return url
    host, slash, path = rest.partition("/")
    return f"{head.replace('t', 'x', 2)}://{host.replace('.', '[.]')}{slash}{path}"


def allow(url: str, reason: str, who: str = "operator") -> bool:
    """An operator's decision that a flagged URL is safe (a false positive): kept 10 years, and it
    wins over every later flag. Returns False for a URL that is not a clean http(s) URL.
    """
    clean = normalize(url)
    if not clean:
        return False
    record = {
        "url": clean,
        "allowed_by": who[:80],
        "reason": reason[:300],
        "allowed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "was": flagged_record(clean),
    }
    _cache_put(_ALLOWED_SOURCE, clean, json.dumps(record), _RECORD_DAYS)
    return True


# a kept page is nh3 output: every attribute value is double-quoted, ``"`` inside it escaped
_LINK_ATTR_RE = re.compile(r'\s(href|src)="([^"]*)"')


# a web scheme followed by any number of slashes (backslashes are slashes by then): a browser
# skips them all and reads the host next
_SCHEME_SLASHES_RE = re.compile(r"(?i)^(https?):/*")
_HAS_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_LEADING_SLASHES_RE = re.compile(r"^/{2,}")


def _browser_form(raw: str) -> str:
    """The value as a browser reads an http(s) link: ``\\`` is ``/`` before the query,
    ``https:host`` / ``https:///host`` mean ``https://host``, and a scheme-relative ``///host``
    means ``//host`` (a browser skips every slash before the host)."""
    cut = min((i for i in (raw.find("?"), raw.find("#")) if i >= 0), default=len(raw))
    raw = raw[:cut].replace("\\", "/") + raw[cut:]
    if _SCHEME_SLASHES_RE.match(raw):
        return _SCHEME_SLASHES_RE.sub(lambda m: m.group(1) + "://", raw)
    if not _HAS_SCHEME_RE.match(raw):
        return _LEADING_SLASHES_RE.sub("//", raw)
    return raw


# a web link we cannot read the way a browser would (userinfo, a host clean_url refuses): it is
# removed, never kept unchecked
_UNREADABLE = "unreadable:"
_C0_AND_SPACE = "".join(chr(c) for c in range(0x21))


def _target(value: str, base: str) -> Optional[str]:
    """The absolute URL a link's attribute value opens, read as a browser does (tab/CR/LF removed
    anywhere, leading/trailing control characters and spaces removed, then the backslash and
    ``https:host`` forms, then resolved against the page); ``_UNREADABLE`` for a web link that
    cannot be read safely; None for a non-web target (``mailto:``, a bare fragment with no page).
    """
    raw = _html.unescape(value)
    raw = raw.replace("\t", "").replace("\n", "").replace("\r", "").strip(_C0_AND_SPACE)
    raw = _browser_form(raw)
    try:
        joined = urljoin(base, raw) if base else raw
    except ValueError:
        joined = raw
    if not joined.lower().startswith(("http://", "https://", "//")):
        return None
    try:
        return normalize(joined) or _UNREADABLE
    except ValueError:
        return _UNREADABLE


def strip_flagged_links(page: str, page_url: str = "") -> Tuple[str, int]:
    """``page`` (allowlist-cleaned HTML, ``html_safety``) with the ``href`` / ``src`` of every
    flagged URL removed - the link text stays. ``page_url`` resolves relative links. At most
    ``_PAGE_MAX`` distinct links are checked (the rest are said, and kept). Returns the page and
    the number of attributes removed."""
    from src.utils.find_queue import clean_for_log

    if not _api_key():  # the check is off: the page is kept as it is
        return page, 0
    targets: Dict[str, None] = {}  # insertion-ordered set
    for m in _LINK_ATTR_RE.finditer(page or ""):
        target = _target(m.group(2), page_url)
        if target and target != _UNREADABLE:
            targets.setdefault(target, None)
    todo = list(targets)
    if len(todo) > _PAGE_MAX:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "link_safety",
            "page_links_unchecked",
            key=("page",),
            every=25,
            note=f"a kept page has more than {_PAGE_MAX} distinct links: only the first were "
            "checked for malicious sites, the rest are kept unchecked. Many of these: raise "
            "link_safety._PAGE_MAX",
            page=clean_for_log(page_url),
            links=len(todo),
        )
    bad = set(flagged(todo[:_PAGE_MAX], f"page {page_url[:150]}")) | {_UNREADABLE}
    counts = {"flagged": 0, "unreadable": 0}

    def drop(m: "re.Match[str]") -> str:
        target = _target(m.group(2), page_url)
        if target not in bad:
            return m.group(0)
        counts["unreadable" if target == _UNREADABLE else "flagged"] += 1
        return ""

    out = _LINK_ATTR_RE.sub(drop, page or "")
    if counts["unreadable"]:
        logger.info(
            "OBSERV link_safety.unreadable_links_removed page=%s n=%d - web links that cannot "
            "be read the way a browser would or that are not safe to fetch (userinfo, an invalid, "
            "private or local host, an over-long URL) are removed, not kept "
            "unchecked",
            clean_for_log(page_url, 120),
            counts["unreadable"],
        )
    return out, counts["flagged"] + counts["unreadable"]
