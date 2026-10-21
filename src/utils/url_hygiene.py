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
    # Non-canonical IPv4 (decimal/hex/octal/short-form) that ipaddress rejects but the
    # socket layer resolves: a host with NO alphabetic char is a would-be IP, never a
    # DNS name -> try to coerce to IPv4 and re-check; reject if uncoercible-but-numeric.
    if ip is None:
        # Non-canonical numeric host that ipaddress rejects but the socket layer still
        # resolves to loopback/metadata: hex (0x7f000001), decimal (2130706433), octal
        # / short-form (0177.0.0.1, 127.1). Detect "looks numeric, not a DNS name".
        import re as _re

        _hex = _re.fullmatch(r"0x[0-9a-f]+", host)
        _numeric = _re.fullmatch(r"[0-9.]+", host) or _re.fullmatch(
            r"0x[0-9a-f.]+", host
        )
        if _hex or _numeric:
            try:
                import socket

                if _hex:
                    ip = ipaddress.ip_address(int(host, 16) & 0xFFFFFFFF)
                else:
                    ip = ipaddress.ip_address(socket.inet_aton(host))
            except (OSError, ValueError):
                return (
                    False  # numeric-but-not-a-parseable-IP -> reject (never a DNS name)
                )
    if ip is not None:
        return ip_is_safe(ip)
    return True  # a DNS hostname (has alphabetic chars) — allowed (see CAVEAT)


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


def clean_url(url: str) -> Optional[str]:
    """Validate + NORMALIZE a URL. Returns the canonical form, or None if the URL is
    malformed, uses a disallowed scheme, targets an unsafe host, or is over-long.

    Normalization: lowercase scheme+host, drop a default port (80/443), strip the
    fragment, DROP the query EXCEPT resource-identity params (id/q/page/... — kept so
    distinct archival documents don't collapse), remaining identity params sorted.
    Path case preserved (can be significant)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > _MAX_URL_LEN:
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
            return _attempt(ip)
        except Exception as exc:  # noqa: BLE001 - try the next vetted IP
            last_exc = exc
    raise last_exc if last_exc else RuntimeError("no vetted IPs to connect")


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
        body = b""
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) > max_bytes:
                logger.warning(
                    "safe_get body exceeded %d bytes, truncating: %s",
                    max_bytes,
                    cleaned,
                )
                break
        # Materialize the bounded body back onto the response so .text/.content work.
        resp._content = body  # noqa: SLF001 - intentional: cap the body
        return resp
    except Exception as exc:  # noqa: BLE001 - any network failure -> None (fail-closed)
        logger.debug("safe_get failed for %s: %s", cleaned, exc)
        return None


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
