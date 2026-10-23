"""Clean-URL hygiene: validate + normalize a URL before it is fetched, Grok-evaluated,
or stored as a URL-registry key (URL_SOURCED_ENRICHMENT_DESIGN.md).

A URL entering the registry becomes (a) a fetch target, (b) a DynamoDB key component,
and (c) provenance written onto a record. So it must be CLEAN before any of that:

  * well-formed http/https with a real host,
  * SSRF-safe — reject localhost / loopback / private + link-local ranges / the cloud
    metadata IP / non-http schemes (javascript:, data:, file:, ...),
  * normalized — lowercase scheme+host, drop default ports, strip the fragment and
    common tracking params — so the same resource dedups to ONE registry key.

Pure functions, no I/O, fail-closed (invalid -> None / False).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import unicodedata
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")

# Query params that carry RESOURCE IDENTITY (not tracking) — these are KEPT; every
# other query param is dropped. Rationale: for this corpus, document identity often
# lives in the query (NARA Catalog ?q=, record viewers ?id=, scanned-page ?page=), so
# a blind "drop everything after ?" would merge DISTINCT sources into one registry
# key. We default to dropping the query but preserve this identity allowlist.
_IDENTITY_PARAMS = {
    "id",
    "q",
    "query",
    "search",
    "page",
    "p",  # common page alias
    "pg",  # common page alias
    "docid",
    "doc",
    "recordid",
    "naid",  # NARA archival identifier
    "arcid",  # legacy NARA
    "identifier",
    "title",
    "oldid",  # wikipedia revision identity
    "v",  # some viewers/video identity
}

_MAX_URL_LEN = 2048


def _host_is_safe(host: str) -> bool:
    """Reject SSRF-risky hosts: localhost, the cloud metadata IP, and any private/
    loopback/link-local/reserved IP — INCLUDING non-canonical encodings (decimal
    2130706433, hex 0x7f000001, octal 0177.0.0.1, short-form 127.1, trailing-dot
    169.254.169.254.) that `ipaddress` refuses to parse but the socket layer still
    resolves to loopback/metadata. A genuine multi-label DNS hostname is allowed.

    CAVEAT: this validates the host STRING only. A DNS name that passes here can still
    RESOLVE to a private IP (DNS rebinding). The fetch layer MUST additionally resolve
    the host and re-check every returned address (and re-validate each redirect hop)
    before connecting — clean_url alone does not make a URL fetch-safe."""
    if not host:
        return False
    # Normalize UNICODE homographs BEFORE classification: NFKC collapses ideographic dots (。 U+3002)
    # and fullwidth digits (１２７) to ASCII, and IDNA maps a unicode label to its ASCII form — so a
    # host like '127。0。0。1' / '１２７.0.0.1' (which getaddrinfo resolves to 127.0.0.1) is caught by
    # the numeric detection below instead of passing as a DNS name. A legitimate IDN (münchen.de)
    # still passes via its punycode/decoded ASCII. Fail-safe: on an IDNA error keep the NFKC form.
    host = unicodedata.normalize("NFKC", host)
    try:
        host = host.encode("idna").decode("ascii")
    except (
        Exception
    ):  # noqa: BLE001 - not IDNA-encodable (e.g. an IP literal) -> use NFKC form
        pass
    host = host.lower().strip("[]").rstrip(".")  # strip IPv6 brackets + trailing dot
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain"):
        return False
    if host == "169.254.169.254":  # cloud instance metadata
        return False
    # Canonical IP literal?
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    # Non-canonical numeric host (decimal/hex/dotted-hex/mixed-radix/octal/short-form) that
    # ipaddress rejects but the socket layer still resolves to loopback/metadata.
    if ip is None:
        ip = _coerce_numeric_host(host)
        if ip is False:  # looked numeric but uncoercible -> reject (never a DNS name)
            return False
    if ip is not None:
        return ip_is_safe(ip)
    return True  # a DNS hostname (has alphabetic chars) — allowed (see CAVEAT)


# A numeric host LABEL: a 0x-hex literal, OR all digits (decimal; a leading-zero form like '0177'
# is read as OCTAL by inet_aton). A label with any other alpha char is a real DNS name.
_NUM_HOST_LABEL = re.compile(r"^(0[xX][0-9a-fA-F]+|[0-9]+)$")


def _coerce_numeric_host(host: str):
    """If ``host`` is a would-be numeric IPv4 in ANY non-canonical encoding (hex 0x7f000001, dotted-
    hex 0x7f.0x0.0x0.0x1, mixed-radix 127.0.0.0x1, decimal 2130706433, octal 0177.0.0.1, short-form
    127.1), return its ipaddress object; return None if it's a genuine DNS name (has a non-numeric
    label); return False if it LOOKS numeric but can't be coerced (reject — never a DNS name).
    """
    import socket

    labels = host.split(".")
    if not (labels and all(_NUM_HOST_LABEL.match(lbl) for lbl in labels)):
        return None  # a real DNS name
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except (OSError, ValueError):
        try:  # inet_aton rejects a single bare 0x… ; try a plain int coerce
            return ipaddress.ip_address(int(host, 0) & 0xFFFFFFFF)
        except (OSError, ValueError):
            return False


def ip_is_safe(ip) -> bool:
    """True iff ``ip`` (an ipaddress object or string) is a PUBLIC address — not
    private/loopback/link-local/reserved/multicast/unspecified/metadata. Shared by the
    host-string check and the fetcher's resolved-IP (DNS-rebinding) check."""
    try:
        if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            ip = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    if str(ip) == "169.254.169.254":  # cloud metadata (also caught by link_local)
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolved_host_is_safe(host: str):
    """DNS-rebinding defense (the FETCHER's responsibility per clean_url's caveat):
    resolve ``host`` and require EVERY resolved address to be public (ip_is_safe).
    Returns the list of resolved IP strings if ALL are safe, else None (unsafe or
    unresolvable). The caller should PIN the connection to one of these vetted IPs so a
    rebind between check and connect cannot slip through."""
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 - unresolvable -> treat as unsafe/transient
        return None
    ips = []
    for info in infos:
        addr = str(info[4][0])
        ip = addr.split("%", 1)[0]  # strip IPv6 zone id
        if not ip_is_safe(ip):
            return None
        ips.append(ip)
    return ips or None


def is_clean_url(url: str) -> bool:
    """True iff ``url`` is a well-formed, SSRF-safe http/https URL. Does NOT normalize."""
    return clean_url(url) is not None


def _prevalidate_url_string(url):
    """clean_url's STRING-level guards (fail-closed, in order): non-str/empty, over-length.
    Returns the stripped URL string, or None to reject."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > _MAX_URL_LEN:
        return None
    return url


def _parse_and_validate_url(url):
    """Parse + run clean_url's reject guards in order; return ``(scheme, parsed)`` or None.
    Preserves EXACTLY clean_url's fail-closed sequence: non-str/empty, over-length, parse
    failure, disallowed scheme, missing netloc/hostname, unsafe host, credentials-in-URL.
    """
    url = _prevalidate_url_string(url)
    if url is None:
        return None
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001 - any parse failure -> not clean
        return None

    scheme = (p.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return None
    if not p.netloc or not p.hostname:
        return None
    if not _host_is_safe(p.hostname):
        return None
    # Reject credentials-in-URL (an anti-pattern + a dedup hazard) BEFORE rebuilding netloc.
    if p.username or p.password:
        return None
    return scheme, p


def clean_url(url: str) -> Optional[str]:
    """Validate + NORMALIZE a URL. Returns the canonical form, or None if the URL is
    malformed, uses a disallowed scheme, targets an unsafe host, or is over-long.

    Normalization: lowercase scheme+host, drop a default port (80/443), strip the
    fragment, DROP the query EXCEPT resource-identity params (id/q/page/... — kept so
    distinct archival documents don't collapse), remaining identity params sorted.
    Path case preserved (can be significant)."""
    validated = _parse_and_validate_url(url)
    if validated is None:
        return None
    scheme, p = validated

    netloc = _normalize_netloc(scheme, p)
    query = _filter_identity_query(p.query)
    path = p.path or "/"
    # Drop RFC-3986 path params (the ';' segment, e.g. ';jsessionid=') — session tracking lives
    # there and would otherwise leak into the dedup key. Fragment dropped.
    return urlunparse((scheme, netloc, path, "", query, ""))


def _normalize_netloc(scheme: str, p) -> str:
    """Lowercase host + drop a default port (80/443). Caller has already rejected userinfo."""
    host = p.hostname.lower()
    if p.port and not (
        (scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)
    ):
        return f"{host}:{p.port}"
    return host


def _filter_identity_query(raw_query: str) -> str:
    """Drop the query EXCEPT resource-identity params (id/q/page/...): tracking/session noise
    collapses but distinct documents (NARA ?id=, catalog ?q=, viewer ?page=) stay distinct.
    Kept params are lowercased + sorted for a stable dedup key."""
    return urlencode(
        sorted(
            (k.lower(), v)
            for k, v in parse_qsl(raw_query, keep_blank_values=True)
            if k.lower() in _IDENTITY_PARAMS
        )
    )


# --- SSRF-safe FETCH layer -------------------------------------------------------
# clean_url / resolved_host_is_safe are PURE validators (no I/O). The functions below
# are the fetch layer that the caveat in _host_is_safe demands: they clean the URL,
# re-resolve the host and reject non-public IPs (DNS-rebinding), and REFUSE redirects
# (a redirect can bounce a clean host to 169.254.169.254 / an internal address). Every
# untrusted-URL GET in the pipeline MUST route through here (or images.fetch_image_bytes
# for image bytes) rather than calling requests.get directly.

_SAFE_FETCH_TIMEOUT = 30
_SAFE_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SAFE_FETCH_MAX_BYTES = 10 * 1024 * 1024  # 10MB cap on a fetched page/body


def _pinned_get(cleaned_url, host, vetted_ips, *, timeout, headers):
    """SSRF-safe GET that PINS the TCP connection to a VETTED resolved IP, closing the DNS-rebinding
    TOCTOU: ``resolved_host_is_safe`` vetted the IPs, but a plain ``requests.get(hostname_url)``
    re-resolves at connect time, so a short-TTL attacker name could answer public on the check and
    loopback/metadata on the connect. We instead connect to one of the already-vetted IPs while
    keeping the ``Host`` header (and, for https, the TLS SNI + cert hostname) = the original host.

    Always allow_redirects=False (a redirect re-opens SSRF) and streamed (caller caps the body).
    Tries each vetted IP in turn (all are public/safe) so a single unreachable address (e.g. an
    unroutable IPv6 first record) doesn't fail the fetch the way a hard first-IP pin would.
    """
    import ipaddress

    import requests
    from requests.adapters import HTTPAdapter

    scheme = urlparse(cleaned_url).scheme
    parts = urlparse(cleaned_url)
    port = parts.port
    req_headers = dict(headers or {})
    req_headers["Host"] = parts.netloc  # preserve vhost routing + logging

    class _PinnedHTTPSAdapter(HTTPAdapter):
        """Open the pool against the vetted IP but keep TLS SNI + cert check against ``host``."""

        def init_poolmanager(self, *a, **kw):
            kw["server_hostname"] = host  # TLS SNI + cert hostname = original host
            kw["assert_hostname"] = host
            return super().init_poolmanager(*a, **kw)

    def _attempt(ip):
        is_v6 = ":" in ip and isinstance(
            ipaddress.ip_address(ip), ipaddress.IPv6Address
        )
        ip_hostpart = f"[{ip}]" if is_v6 else ip
        netloc_ip = f"{ip_hostpart}:{port}" if port else ip_hostpart
        ip_url = parts._replace(netloc=netloc_ip).geturl()
        sess = requests.Session()
        if scheme == "https":
            sess.mount("https://", _PinnedHTTPSAdapter())
        try:
            return sess.get(
                ip_url,
                timeout=timeout,
                headers=req_headers,
                allow_redirects=False,
                stream=True,
            )
        finally:
            sess.close()

    last_exc = None
    for ip in vetted_ips:
        try:
            resp = _attempt(ip)
            _record_pinned_fetch(cleaned_url, getattr(resp, "status_code", None), None)
            return resp
        except Exception as exc:  # noqa: BLE001 - try the next vetted IP
            last_exc = exc
    _record_pinned_fetch(cleaned_url, None, last_exc)
    raise last_exc if last_exc else RuntimeError("no vetted IPs to connect")


def _record_pinned_fetch(cleaned_url, status, exc) -> None:
    """Record a fetch-provenance event for an SSRF-pinned untrusted-source GET. Fail-safe;
    status only (the body is streamed/byte-capped by the caller, never consumed here).
    """
    try:
        from urllib.parse import urlparse as _up

        from src.utils.fetch_provenance import (
            ERROR,
            HIT,
            MISS,
            TEXTUAL,
            record_fetch_event,
        )

        if exc is not None:
            outcome, summary = ERROR, None
        elif status is not None and 200 <= status < 300:
            outcome, summary = HIT, f"status={status}"
        else:
            outcome, summary = MISS, f"status={status}"
        record_fetch_event(
            provider=f"pinned:{(_up(cleaned_url).hostname or '')[:60]}",
            endpoint=cleaned_url,
            kind=TEXTUAL,
            http_status=status,
            outcome=outcome,
            result_summary=summary,
            error=(f"{type(exc).__name__}: {exc}" if exc is not None else None),
        )
    except Exception:  # noqa: BLE001 - telemetry must never affect the SSRF fetch
        pass


def safe_get(url, *, timeout=None, headers=None, max_bytes=_SAFE_FETCH_MAX_BYTES):
    """SSRF-safe HTTP GET of an UNTRUSTED URL. Returns a ``requests.Response`` whose
    ``.content``/``.text`` is already bounded to ``max_bytes``, or ``None`` if the URL
    is blocked (bad scheme/host, host resolves to a non-public IP) or the request fails.

    Guards (same discipline as images.fetch_image_bytes):
      1. clean_url -> http(s) only, reject loopback/private/link-local/metadata host,
         reject credentials-in-URL, normalize.
      2. resolved_host_is_safe -> resolve the host, reject if ANY resolved IP is
         non-public (DNS-rebinding defense).
      3. allow_redirects=False -> a redirect could bounce a clean host to an internal
         address, so redirects are refused (surfaced as the 3xx response, not followed).
      4. streamed read with a hard byte cap (defends against decompression/size abuse).

    Callers that only need HTML text should prefer ``fetch_text_safe``.
    """
    import requests  # lazy: keep the pure-validator import path light

    cleaned = clean_url(url)
    if not cleaned:
        logger.debug("safe_get blocked (scheme/host): %s", url)
        return None
    host = (urlparse(cleaned).hostname or "").lower()
    vetted = resolved_host_is_safe(host)
    if vetted is None:
        logger.warning("safe_get blocked (host resolves non-public): %s", host)
        return None
    req_headers = {"User-Agent": _SAFE_FETCH_UA}
    if headers:
        req_headers.update(headers)
    eff_timeout = timeout or _SAFE_FETCH_TIMEOUT
    try:
        resp = _pinned_get(
            cleaned,
            host,
            vetted,
            timeout=eff_timeout,
            headers=req_headers,
        )
        resp._content = _read_capped_body(
            resp, max_bytes, cleaned
        )  # noqa: SLF001 - cap the body
        return resp
    except Exception as exc:  # noqa: BLE001 - any network failure -> None (fail-closed)
        logger.debug("safe_get failed for %s: %s", cleaned, exc)
        return None


def _read_capped_body(resp, max_bytes, cleaned) -> bytes:
    """Stream resp with BOTH a decompressed cap (bounds memory) and a COMPRESSED-read cap (bounds the
    wire read + CPU, so a small highly-compressing gzip body can't force max_bytes of decompression —
    the gzip-bomb guard). resp.raw.tell() is bytes read off the socket (pre-decode)."""
    body = b""
    raw = getattr(resp, "raw", None)
    for chunk in resp.iter_content(8192):
        body += chunk
        try:
            compressed = int(raw.tell()) if raw is not None else 0
        except (
            Exception
        ):  # noqa: BLE001 - raw not tell-able/int-able -> rely on the decompressed cap
            compressed = 0
        if compressed > max_bytes:
            logger.warning(
                "safe_get compressed body exceeded %d bytes (gzip-bomb guard): %s",
                max_bytes,
                cleaned,
            )
            break
        if len(body) > max_bytes:
            logger.warning(
                "safe_get body exceeded %d bytes, truncating: %s", max_bytes, cleaned
            )
            break
    return body


def fetch_text_safe(
    url, *, timeout=None, headers=None, max_bytes=_SAFE_FETCH_MAX_BYTES
):
    """SSRF-safe fetch returning (text, status_code) for a 200 response, else (None, status)
    or (None, None) if blocked/failed. Convenience wrapper over ``safe_get`` for the common
    'grab the HTML of an untrusted page' case."""
    resp = safe_get(url, timeout=timeout, headers=headers, max_bytes=max_bytes)
    if resp is None:
        return None, None
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        return resp.text, 200
    except Exception:  # noqa: BLE001
        return None, resp.status_code
