"""Enrich people_groups with external data (Wikipedia/Grokipedia)."""

import json
import logging
from pathlib import Path
from typing import Optional

from src.grok_client import BatchModeCollecting, GrokClient
from src.schemas import inject_metadata


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _should_re_search(data: dict) -> bool:
    """Check if a not_found entity should be re-searched based on age."""
    from src.enrichment.enrichment_gate import should_re_search

    return should_re_search(data)


from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

SKIP_FILES = frozenset(
    [
        "index.json",
        "not_duplicates.json",
        "duplicate_report.json",
        "related_groups_report.json",
        ".processed_events.json",
    ]
)

PROMPT = """Look up this WWII military unit/organization: {name}

Return JSON with:
- full_name: Official full designation (e.g., "101st Airborne Division (United States)")
- unit_type: division, corps, army, army_group, brigade, regiment, battalion, or other
- nationality: ISO 3166-1 alpha-3 country code
- branch: army, navy, air_force, marines, ss, or other
- parent_unit: Higher formation (e.g., "VII Corps" for a division)
- sub_organizations: Array of major subordinate units (e.g., ["Heer (Army)", "Luftwaffe (Air Force)"]). null if none or unknown
- member_countries: Array of member countries for alliances (e.g., ["Germany", "Italy", "Japan"]). null if not an alliance
- commanding_officers: Array of {{"name": "...", "from_date": "YYYY-MM", "to_date": "YYYY-MM"}} for WWII period only
- notable_operations: Array of operation/battle names this unit participated in
- formed_date: When unit was formed (YYYY or YYYY-MM)
- disbanded_date: When unit was disbanded (YYYY or YYYY-MM, null if still active)
- description: 1-2 sentence summary of the unit's WWII role

CRITICAL: Only include facts you are confident about. Use null for unknown fields.
Return ONLY valid JSON, no markdown."""


_ALLIANCE_MAP = {
    "USA": "Allied Powers",
    "US": "Allied Powers",
    "GBR": "Allied Powers",
    "CAN": "Allied Powers",
    "FRA": "Allied Powers",
    "POL": "Allied Powers",
    "SUN": "Allied Powers",
    "DEU": "Axis Powers",
    "ITA": "Axis Powers",
    "JPN": "Axis Powers",
}


# Nationality code -> Wikipedia-style country suffix for disambiguation.
_COUNTRY_NAME = {
    "USA": "United States",
    "US": "United States",
    "GBR": "United Kingdom",
    "UK": "United Kingdom",
    "CAN": "Canada",
    "AUS": "Australia",
    "NZL": "New Zealand",
    "FRA": "France",
    "POL": "Poland",
    "DEU": "Germany",
    "ITA": "Italy",
    "JPN": "Japan",
    "SUN": "Soviet Union",
    "RUS": "Soviet Union",
    "IND": "India",
    "ZAF": "South Africa",
    "NLD": "Netherlands",
    "BEL": "Belgium",
}


def _build_enrichment_query(data: dict) -> tuple[str, str]:
    """Build a DISAMBIGUATED lookup query from the record's structured identity, to
    avoid Wikipedia disambiguation errors (bare '9th Division' -> wrong country).

    Returns (query, canonical_name):
      - resolve a nickname to its canonical unit name first;
      - append the Wikipedia-style '(Country)' suffix from the record's nationality
        when known (mirrors Wikipedia's own article-title convention);
    Falls back to the plain name when no disambiguators are available.
    """
    name = data.get("name") or data.get("group_name") or ""
    try:
        from src.dedup.unit_key import resolve_nickname

        canonical = resolve_nickname(name) or name
    except Exception:
        canonical = name
    nat = (
        (data.get("nationality") or data.get("country_of_origin") or "").strip().upper()
    )
    country = _COUNTRY_NAME.get(nat)
    query = canonical
    if country and country.lower() not in canonical.lower():
        query = f"{canonical} ({country})"
    return query, canonical


def _enrichment_agrees(data: dict, enrich: dict) -> tuple[bool, str]:
    """Wrong-article GUARD: reject enrichment that CONTRADICTS the record's known facts
    (the hallmark of a mis-resolved Wikipedia disambiguation). Checks nationality and
    echelon/hierarchy; a mismatch where BOTH sides are known -> reject. Unknown on
    either side is permissive. Returns (ok, reason)."""
    # Nationality agreement
    rec_nat = (
        (data.get("nationality") or data.get("country_of_origin") or "").strip().upper()
    )
    got_nat = (enrich.get("nationality") or "").strip().upper()
    if rec_nat and got_nat and rec_nat != got_nat:
        return (
            False,
            f"nationality contradiction (record {rec_nat} vs enrichment {got_nat})",
        )
    # Echelon / hierarchy agreement
    rec_ech = (data.get("military_hierarchy") or "").strip().lower()
    got_ech = (enrich.get("unit_type") or "").strip().lower()
    if rec_ech and got_ech and rec_ech != got_ech:
        return (
            False,
            f"echelon contradiction (record {rec_ech} vs enrichment {got_ech})",
        )
    return True, "agrees"


def _infer_alliance(data, enrich):
    """Set alliance_membership from nationality if not already set."""
    nat = enrich.get("nationality", "")
    if nat and not data.get("alliance_membership"):
        alliance = _ALLIANCE_MAP.get(nat)
        if alliance:
            data["alliance_membership"] = [alliance]


def _promote_enrichment(data):
    """Promote enrichment_data fields to spec-level top-level fields."""
    enrich = data.get("enrichment_data")
    if not enrich or not isinstance(enrich, dict):
        return

    # unit_type is the hierarchy (division, corps, etc.) — group_type is the spec enum
    unit_type = enrich.get("unit_type")
    mapping = {
        "group_type": "military_unit" if unit_type else None,
        "country_of_origin": enrich.get("nationality"),
        "description": enrich.get("description"),
        "military_hierarchy": unit_type,
        "parent_organization": enrich.get("parent_unit"),
        "common_name": enrich.get("full_name"),
        "sub_organizations": enrich.get("sub_organizations"),
        "member_countries": enrich.get("member_countries"),
    }

    for key, value in mapping.items():
        if value and not data.get(key):
            data[key] = value

    _infer_alliance(data, enrich)

    if not data.get("source_language"):
        data["source_language"] = "English"


def enrich_group(group_file: Path, grok_client: GrokClient) -> bool:
    """Enrich a single people_group file. Returns True if enriched."""
    try:
        with open(group_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.debug("Failed to load %s: %s", group_file.name, e)
        return False

    name = data.get("name", "")
    if not name:
        return False

    # Skip if already enriched
    if data.get("enrichment_data"):
        logger.debug("Already enriched: %s", name)
        return False

    if data.get("enrichment_status") == "not_found":
        if not _should_re_search(data):
            logger.debug("Previously searched, not found: %s", name)
            return False
        logger.info("Re-searching (stale not_found): %s", name)

    logger.info("Enriching: %s", name)

    # SOURCE-FIRST gap-fill: recover missing critical fields (nationality, CC parent
    # division) from the RETAINED source text BEFORE the external lookup — Wikipedia
    # can't disambiguate (e.g. '9th Division (United States)') without nationality.
    # Gated (makes a Grok call), gap-fill-only, fail-safe.
    import os as _os

    if _os.getenv("GROUP_SOURCE_RECHECK", "true").lower() == "true":
        try:
            from src.extraction.group_source_recheck import recheck_group_from_source

            n = recheck_group_from_source(data, grok_client)
            if n:
                logger.info("  ✓ Source-recheck recovered %d critical field(s)", n)
        except Exception as e:  # noqa: BLE001 - never block enrichment
            logger.warning("source-recheck skipped: %s", e)

    # Build a DISAMBIGUATED query from the record's identity (nickname-resolved +
    # '(Country)' suffix) so Wikipedia/Grok resolves the correct article, not a
    # disambiguation page or a same-named unit of another nation. (Runs AFTER the
    # source-recheck so the recovered nationality feeds the disambiguation.)
    query, _canonical = _build_enrichment_query(data)

    try:
        enrichment = grok_client.extract_json(
            prompt=PROMPT.format(name=query),
            use_cache=True,
            cache_type="group_enrichment",
        )
    except BatchModeCollecting:
        return False
    except Exception as e:
        # M3: a transient error is NOT a clean negative — do NOT stamp not_found
        # (that suppresses retries for the whole re-search window). Leave the
        # entity unstamped so the next run retries it.
        logger.warning("Group '%s' enrichment errored — leaving for retry: %s", name, e)
        return False

    if not isinstance(enrichment, dict):
        data["enrichment_status"] = "not_found"
        data["last_enrichment_search"] = _today()
        inject_metadata(data, entity="people_groups")
        write_json_with_lock(group_file, data, entity="people_groups")
        return False

    # Wrong-article GUARD: if the returned enrichment CONTRADICTS the record's known
    # nationality/echelon, it is almost certainly a mis-resolved disambiguation (wrong
    # same-named unit). Do NOT stamp the wrong data; mark 'ambiguous' (retriable, visible).
    ok, reason = _enrichment_agrees(data, enrichment)
    if not ok:
        logger.warning("  ⚠ Enrichment rejected for %s — %s", name, reason)
        data["enrichment_status"] = "ambiguous"
        data["enrichment_ambiguity_reason"] = reason
        data["last_enrichment_search"] = _today()
        inject_metadata(data, entity="people_groups")
        write_json_with_lock(group_file, data, entity="people_groups")
        return False

    _apply_group_enrichment(data, enrichment, group_file, name)
    return True


def _apply_group_enrichment(
    data: dict, enrichment: dict, group_file: Path, name: str
) -> None:
    """Apply group enrichment with a DIFF guard: if byte-identical to what we already
    hold, refresh only the staleness stamp (limit updates); else rewrite + promote.
    Groups previously overwrote enrichment_data unconditionally on every re-search.
    (The wrong-article guard already ran in enrich_group.)"""
    from src.enrichment.enrichment_gate import diff_enrichment

    unchanged = not diff_enrichment(
        {"enrichment_data": data.get("enrichment_data")},
        {"enrichment_data": enrichment},
    )
    data["enrichment_status"] = "enriched"
    data["last_enrichment_search"] = _today()
    if not data.get("group_name") and data.get("name"):
        data["group_name"] = data["name"]  # alias of name per spec
    if unchanged:
        inject_metadata(data, entity="people_groups")
        write_json_with_lock(group_file, data, entity="people_groups")
        logger.debug("  = Enrichment unchanged for %s (stamp refreshed only)", name)
        return
    data["enrichment_data"] = enrichment
    _promote_enrichment(data)
    inject_metadata(data, entity="people_groups")
    write_json_with_lock(group_file, data, entity="people_groups")
    logger.info("  ✓ Enriched %s", name)


def enrich_all_groups(
    groups_dir: Path,
    grok_client: GrokClient,
    max_groups: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich all people_groups in directory.

    Returns number of groups enriched.
    """
    if not groups_dir.exists():
        logger.error("Directory not found: %s", groups_dir)
        return 0

    group_files = [f for f in groups_dir.glob("*.json") if f.name not in SKIP_FILES]

    if max_groups:
        group_files = group_files[:max_groups]

    logger.info("Enriching %d people groups...", len(group_files))

    enriched = 0
    processed = 0
    total = len(group_files)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_group, gf, grok_client): gf for gf in group_files}
        for future in as_completed(futures):
            processed += 1
            try:
                if future.result():
                    enriched += 1
            except Exception as e:
                logger.warning("Failed to enrich group %s: %s", futures[future].stem, e)
            if processed % 10 == 0 or processed == total:
                logger.info(
                    "  Groups progress: %d/%d done (%d enriched)",
                    processed,
                    total,
                    enriched,
                )

    logger.info("Group enrichment complete: %d/%d enriched", enriched, len(group_files))
    return enriched
