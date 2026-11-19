"""Which sources are trusted and how far links are followed - read in ONE place (task 1.9, owner
decisions 2026-11-17).

``trusted_sources`` (config.yaml): sources whose cited documents are queued for ingestion with no
URL Grok check and no human URL review - HyperWar volumes (HTML and PDF) and NARA records today;
adding one is a config edit. Trust covers the source, not its content: everything inside is still
classified by Grok in Phases 1-3, and its links to untrusted sites follow the review rule. A
trusted record found by a SEARCH still needs the Grok check that it is the cited document.

``links.follow`` (config.yaml): how far a followed link's own links are followed, and how many per
page, separately for links on the parent page's own site (``same_domain``) and elsewhere
(``external``). Default 1 hop and 20 links per page for each.

Both are logged once per run (``log_policy``), so a run says what it trusted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

SAME_DOMAIN, EXTERNAL = "same_domain", "external"
DEFAULT_FOLLOW = {"max_hops": 1, "max_per_page": 20}
# hard ceilings: a config typo must not turn link following into a crawl
MAX_HOPS_CEILING = 3
MAX_PER_PAGE_CEILING = 200


@dataclass(frozen=True)
class TrustedSource:
    name: str
    matches: Tuple[Tuple[str, str], ...]  # (host, path prefix)
    forms: Tuple[str, ...]


def _config(config: Optional[dict]) -> Optional[dict]:
    """``config``, else config.yaml - or None when it cannot be read (nothing is trusted then,
    and the failure is not cached: the next call tries again)."""
    if config is not None:
        return config
    try:
        from src.utils.config import load_config

        return load_config() or {}
    except Exception as e:  # noqa: BLE001 - no config: nothing trusted (the safe side)
        logger.warning(
            "OBSERV link_policy.config_unreadable err=%s status=warn - nothing is trusted "
            "and links use the default limits until config.yaml can be read",
            type(e).__name__,
        )
        return None


_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._~/-]*/")


def _parse_match(entry: Any) -> Optional[Tuple[str, str]]:
    """``host/path/prefix/`` -> (host without ``www.``, "/path/prefix/"); None when it is not
    one. The path must be a real prefix ending in "/" (a bare host would trust a whole site, and
    ``/hyperwar`` would also match ``/hyperwarez``), with no dot segments or escapes."""
    if not isinstance(entry, str):
        return None
    host, _, path = entry.strip().partition("/")
    host = host.lower()
    path = "/" + path
    if not _HOST.fullmatch(host) or not _SAFE_PATH.fullmatch(path) or path == "/":
        return None
    if any(seg in (".", "..") for seg in path.split("/")):
        return None
    return (host[4:] if host.startswith("www.") else host), path


# the process's config.yaml list, kept after the first successful read
_loaded: Dict[str, Tuple[TrustedSource, ...]] = {}


def trusted_sources(config: Optional[dict] = None) -> Tuple[TrustedSource, ...]:
    """The configured trusted sources (an entry that is not well formed is skipped, loudly).
    Without ``config`` config.yaml is read once and kept; a failed read is not kept."""
    if config is not None:
        return _parse_sources(config)
    if "sources" not in _loaded:
        loaded = _config(None)
        if loaded is None:
            return ()
        if "trusted_sources" not in loaded:
            logger.warning(
                "OBSERV link_policy.not_configured status=warn - config.yaml has no "
                "trusted_sources, so nothing is trusted: copy the block from config.yaml.example"
            )
        _loaded["sources"] = _parse_sources(loaded)
    return _loaded["sources"]


def reset() -> None:
    """Forget the cached list (tests; a config change in a long-lived process)."""
    _loaded.clear()


def _parse_sources(config: dict) -> Tuple[TrustedSource, ...]:
    out = []
    for item in config.get("trusted_sources") or []:
        src = _parse_source(item)
        if src is None:
            logger.warning(
                "OBSERV link_policy.trusted_source_invalid entry=%s status=warn - skipped; fix "
                "trusted_sources in config.yaml (name + match: host/path)",
                str(item)[:120],
            )
            continue
        out.append(src)
    return tuple(out)


def _parse_matches(raw: Any) -> Optional[Tuple[Tuple[str, str], ...]]:
    """A ``match`` value (one string or a list) as (host, path) pairs; None if any is bad."""
    parsed = [_parse_match(m) for m in (raw if isinstance(raw, list) else [raw])]
    valid = tuple(m for m in parsed if m)
    return valid if valid and len(valid) == len(parsed) else None


def _parse_source(item: Any) -> Optional[TrustedSource]:
    """One ``trusted_sources`` entry, or None when it is not well formed."""
    if not isinstance(item, dict):
        return None
    name, matches = item.get("name"), _parse_matches(item.get("match"))
    if not isinstance(name, str) or not name or matches is None:
        return None
    forms = tuple(str(f) for f in (item.get("forms") or ["html"]) if f)
    return TrustedSource(name, matches, forms)


# anything a client could read differently from urlparse, or that could leave the path prefix
# once a server decodes it: never trusted
_UNSAFE_URL = re.compile(
    r"[\\\x00-\x1f\x7f\s]|%2e|%2f|%5c|%25|%[01][0-9a-f]|%7f|%[c-f][0-9a-f]%[89ab]", re.I
)  # raw or encoded separators/controls, double-encoding (%25) and encoded non-ASCII dots


def _plain_parts(url: Any) -> Optional[Any]:
    """``urlparse(url)`` for a plain http(s) URL with a host, no userinfo and a default port;
    None for anything else (or anything clients could read differently)."""
    if not isinstance(url, str) or _UNSAFE_URL.search(url):
        return None
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError:
        return None
    plain = (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and port in (None, 80, 443)
        and ";" not in parts.path
    )
    return parts if plain else None


def _host_path(url: str) -> Optional[Tuple[str, str]]:
    """(host without ``www.``, decoded path) of a plain http(s) URL with no dot segments and no
    trailing-dot host, or None."""
    parts = _plain_parts(url)
    if parts is None:
        return None
    host = parts.hostname.lower()
    path = unquote(parts.path or "/")
    if host.endswith(".") or any(seg in (".", "..") for seg in path.split("/")):
        return None
    return (host[4:] if host.startswith("www.") else host), path


def trust_basis(url: str, config: Optional[dict] = None) -> Optional[str]:
    """The name of the trusted source ``url`` belongs to, or None. The URL's host must BE the
    source's host (``www.`` ignored; other subdomains are other sites) AND its path must start
    with the source's path, compared case for case - so ``ibiblio.org/hyperwar/`` trusts HyperWar
    volumes, not the rest of ibiblio.org. A URL with dot segments, escapes, userinfo, an unusual
    port or characters clients read differently is never trusted."""
    hp = _host_path(url)
    if hp is None:
        return None
    host, path = hp
    for src in trusted_sources(config):
        for m_host, m_path in src.matches:
            if host == m_host and path.startswith(m_path):
                return src.name
    return None


def link_scope(url: str, parent_url: str) -> str:
    """``same_domain`` when ``url`` is on the parent page's site (the same host, ``www.``
    ignored, or a subdomain of it), else ``external``."""
    a, b = _host_path(url), _host_path(parent_url)
    if a is None or b is None:
        return EXTERNAL
    host, parent = a[0], b[0]
    return SAME_DOMAIN if host == parent or host.endswith("." + parent) else EXTERNAL


def follow_limits(scope: str, config: Optional[dict] = None) -> Dict[str, int]:
    """``{max_hops, max_per_page}`` for ``scope`` (same_domain / external): the config value when
    it is a non-negative whole number within the ceilings, else the default."""
    raw = (((_config(config) or {}).get("links") or {}).get("follow") or {}).get(scope)
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, ceiling in (
        ("max_hops", MAX_HOPS_CEILING),
        ("max_per_page", MAX_PER_PAGE_CEILING),
    ):
        v = raw.get(key, DEFAULT_FOLLOW[key])
        ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= ceiling
        if not ok:
            logger.warning(
                "OBSERV link_policy.follow_invalid scope=%s key=%s value=%r status=warn - "
                "using %d; set links.follow.%s.%s to a whole number 0-%d in config.yaml",
                scope,
                key,
                str(v)[:20],
                DEFAULT_FOLLOW[key],
                scope,
                key,
                ceiling,
            )
        out[key] = v if ok else DEFAULT_FOLLOW[key]
    return out


def log_policy(config: Optional[dict] = None) -> None:
    """One OBSERV line per run: what is trusted and how far links are followed - the values in
    force (the same cached list ``trust_basis`` uses when no config is passed)."""
    sources = trusted_sources(config)
    trusted = ",".join(
        f"{s.name}:{'+'.join(h + p for h, p in s.matches)}" for s in sources
    )
    logger.info(
        "OBSERV link_policy.loaded trusted=%r same_domain=%s external=%s status=ok",
        trusted or "none",
        follow_limits(SAME_DOMAIN, config),
        follow_limits(EXTERNAL, config),
    )
