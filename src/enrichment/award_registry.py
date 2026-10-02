"""Load the award-source registry (config/award_sources.yaml) and build the
per-nationality source adapters that enrich_person_awards consumes.

The registry is DATA; this is the thin code that turns a registry row into a live
``AwardCitationSource``. Each row names an ``adapter``; only adapters with a
registered implementation are instantiated — unimplemented ones (declared in the
registry but not yet coded) are skipped with a debug log, so the catalog can list
sources ahead of their adapters without breaking enrichment.

Selection is by AWARDING POWER / nationality code (see award_sources.awarding_power):
``sources_for(code)`` returns the enabled, implemented adapters whose ``nationality``
matches ``code``, so each award routes to the record system of the power that issued it.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from src.enrichment.award_sources import AwardCitationSource, canonical_nationality

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY = Path("config/award_sources.yaml")


def _registry_path() -> Path:
    return Path(os.getenv("AWARD_SOURCES_REGISTRY", str(DEFAULT_REGISTRY)))


@lru_cache(maxsize=4)
def load_registry(path: Optional[str] = None) -> tuple:
    """Load + parse the registry YAML into an immutable tuple of source dicts.

    Cached (the file is static per run). Returns () if the file is missing.
    """
    p = Path(path) if path else _registry_path()
    if not p.is_file():
        logger.warning("Award registry not found at %s", p)
        return ()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return tuple(data.get("sources", []))


# adapter name -> builder(entry, storage) -> AwardCitationSource | None
def _access_profile(entry: dict) -> dict:
    """Per-source optimized access profile from the registry (crawl_delay + optional
    header overrides). Headers default to the shared browser profile; a source may
    override UA / Accept-Language for best fit with its site."""
    extra_headers = {}
    if entry.get("user_agent"):
        extra_headers["User-Agent"] = entry["user_agent"]
    if entry.get("accept_language"):
        extra_headers["Accept-Language"] = entry["accept_language"]
    return {
        "crawl_delay": entry.get("crawl_delay", 2.0),
        "extra_headers": extra_headers or None,
    }


def _build_hall_of_valor(entry, storage):
    from src.enrichment.award_hall_of_valor import HallOfValorSource

    cache_dir = Path(os.getenv("AWARD_CACHE_DIR", "cache/hall_of_valor"))
    profile = _access_profile(entry)
    return HallOfValorSource(
        cache_dir,
        storage=storage,
        source_id=entry.get("id"),
        crawl_delay=profile["crawl_delay"],
        extra_headers=profile["extra_headers"],
    )


# Adapters that are declared in the registry but not yet implemented resolve to None
# here (skipped). Add a builder entry when an adapter is coded.
_ADAPTER_BUILDERS: Dict[str, Callable] = {
    "hall_of_valor": _build_hall_of_valor,
}


def build_source(entry: dict, storage=None) -> Optional[AwardCitationSource]:
    """Instantiate the adapter for one registry entry, or None if its adapter is
    not implemented / the entry is disabled."""
    if not entry.get("enabled"):
        return None
    adapter = entry.get("adapter")
    builder = _ADAPTER_BUILDERS.get(adapter)
    if builder is None:
        logger.debug(
            "Award source '%s' adapter '%s' not implemented yet; skipping",
            entry.get("id"),
            adapter,
        )
        return None
    try:
        return builder(entry, storage)
    except Exception as e:  # noqa: BLE001 - a bad source must not break the rest
        logger.warning("Failed to build award source '%s': %s", entry.get("id"), e)
        return None


def sources_for(
    code: Optional[str], storage=None, registry_path: Optional[str] = None
) -> List[AwardCitationSource]:
    """Return the enabled + implemented adapters for a nationality/awarding-power
    code (e.g. 'USA', 'GBR', 'SUN'). Spelling-tolerant via canonical_nationality."""
    canon = canonical_nationality(code) or code
    out: List[AwardCitationSource] = []
    for entry in load_registry(registry_path):
        entry_code = canonical_nationality(entry.get("nationality")) or entry.get(
            "nationality"
        )
        if entry_code != canon:
            continue
        src = build_source(entry, storage)
        if src is not None:
            out.append(src)
    return out


def make_selector(storage=None, registry_path: Optional[str] = None):
    """Return a selector(code)->sources closure for enrich_person_awards."""

    def _selector(code: Optional[str]) -> List[AwardCitationSource]:
        return sources_for(code, storage=storage, registry_path=registry_path)

    return _selector


@lru_cache(maxsize=4)
def award_domains(registry_path: Optional[str] = None) -> frozenset:
    """Hostnames owned by award sources (from every registry base_url, plus known
    valor aliases). OpenSERP should SKIP these — they are sourced authoritatively +
    politely by the award adapters, so search-engine scraping of them is redundant
    and lower quality. Returns a frozenset of bare hostnames (no scheme)."""
    from urllib.parse import urlparse

    hosts = set()
    for entry in load_registry(registry_path):
        base = entry.get("base_url") or ""
        host = urlparse(base).netloc
        if host:
            hosts.add(host.lower())
            hosts.add(host.lower().removeprefix("www."))
    # Known valor domains the old search-engine path targeted (now direct/offline).
    hosts.update({"valor.militarytimes.com", "valor.defense.gov", "homeofheroes.com"})
    return frozenset(hosts)


def is_award_domain(url: str, registry_path: Optional[str] = None) -> bool:
    """True if ``url``'s host belongs to an award source (OpenSERP should skip it)."""
    from urllib.parse import urlparse

    host = (urlparse(url).netloc or "").lower()
    if not host:
        return False
    domains = award_domains(registry_path)
    return host in domains or host.removeprefix("www.") in domains
