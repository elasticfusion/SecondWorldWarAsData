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
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    return True  # a DNS hostname (has alphabetic chars) — allowed (see CAVEAT)


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

    # Rebuild netloc: lowercase host, keep userinfo out (reject it — credentials in a
    # URL are an anti-pattern + a dedup hazard), drop default ports.
    if p.username or p.password:
        return None
    host = p.hostname.lower()
    netloc = host
    if p.port and not (
        (scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)
    ):
        netloc = f"{host}:{p.port}"

    # Drop the query EXCEPT resource-identity params (option 2): everything after '?'
    # is ignored unless it's an identity param, so tracking/session noise collapses but
    # distinct documents (NARA ?id=, catalog ?q=, viewer ?page=) stay distinct. Kept
    # params are sorted for a stable key.
    query = urlencode(
        sorted(
            (k.lower(), v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() in _IDENTITY_PARAMS
        )
    )
    path = p.path or "/"
    # Drop RFC-3986 path params (the ';' segment, e.g. ';jsessionid=') — session
    # tracking lives there and would otherwise leak into the dedup key. Fragment dropped.
    return urlunparse((scheme, netloc, path, "", query, ""))
