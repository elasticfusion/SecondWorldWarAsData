#!/usr/bin/env python3
"""Post-Phase-3 data validation (spec: docs/current/PHASE3_DATA_VALIDATION.md).

Runs AFTER Phase 3 (enrichment) finishes and asserts the ENRICHED entity data is still
schema-correct, version-matched, traceable to source, and that the enrichments that DID occur are
internally consistent (an enriched record carrying an external source must carry the source link).
A-level failures are HARD (non-zero exit + ``VALIDATION FAIL``); B-level issues are WARNINGS.

Deliberately REUSES the Phase-2 validator's building blocks (no re-derivation) so the two stay in
lock-step on schema/version/traceback/referential-integrity semantics:
  • A1/A2 schema+version    : validate_phase2.check_schema_and_version
  • A3    source traceback  : validate_phase2.check_source_traceback
  • A3/B1 referential audit  : validate_phase2.check_referential_integrity (0-dangling gate)
  • Report / _entity_files / _load helpers

Phase-3-SPECIFIC checks layered on top:
  • P3-A5 enriched-record source linkage: a record whose ``enrichment_status == "enriched"`` MUST
    carry a resolvable source LINK — otherwise the enrichment is un-attributable (the standalone
    analogue of the Q3 write-guard). HARD.
  • P3-B4 coverage floors (WARN, never hard — external services may be down in dev):
      - places: 0-of-N geocoded when places exist (geocoder cascade down).
      - bibliography / weather: 0-of-N enriched when records exist.
    OpenSERP coverage is intentionally NOT gated (fully service-dependent, often off in dev).

Usage:
  python -m scripts.validate_phase3 [--output-root output] [--strict-coverage]
    --strict-coverage : promote the P3-B4 0-of-N coverage warnings to HARD failures.
Exit: 0 = PASS (no A failures); 1 = FAIL (>=1 A failure).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

# Reuse the Phase-2 validator wholesale (same repo, same semantics).
from scripts.validate_phase2 import (  # type: ignore[import-not-found]
    Report,
    _entity_files,
    _load,
    check_referential_integrity,
    check_schema_and_version,
    check_source_traceback,
)

# --- P3-A5: enriched-record source linkage -----------------------------------------------------
# Fields that signal an external-source LINK; checked at top level and inside biographical_profile.
_LINK_FIELDS = ("wikipedia_url", "grokipedia_url", "source_url", "archive_url")
# Entity dirs gated by P3-A5. ONLY entities whose ``enrichment_status == "enriched"`` means a
# DESCRIPTIVE external-source enrichment (so a link is genuinely expected) are listed here:
#   • people / people_groups : Wikipedia/Grokipedia descriptive enrichment -> link expected.
#   • equipment              : Wikipedia/Grokipedia enrichment; link lives nested under
#                              external_data (the schema forbids a top-level *_url) -> see _has_link.
# Deliberately EXCLUDED (would be false-fails):
#   • places        : Phase-3 place "enriched" means hierarchy + historical_names (structured, and
#                     deliberately link-free — a place with no Wikipedia article is still enriched).
#   • bibliography  : has NO enrichment_status field (uses search_status) -> never matches this gate.
#   • weather       : attribution is covered at the aggregate level by P3-B4 (NOAA coverage).
_ENRICHED_ENTITIES = {
    "people": "output/people/*.json",
    "people_groups": "output/people_groups/*.json",
    "equipment": "output/equipment/*.json",
}


def _has_link(rec: Dict[str, Any]) -> bool:
    if any(rec.get(k) for k in _LINK_FIELDS):
        return True
    bp = rec.get("biographical_profile")
    if isinstance(bp, dict) and any(bp.get(k) for k in _LINK_FIELDS):
        return True
    # Equipment stores its source link ONLY nested under external_data (the equipment output schema
    # has additionalProperties:false and forbids a top-level wikipedia_url/grokipedia_url).
    ext = rec.get("external_data")
    if isinstance(ext, dict):
        if any(ext.get(k) for k in _LINK_FIELDS):
            return True
        for src in ext.get("additional_sources") or []:
            if isinstance(src, dict) and src.get("url"):
                return True
    urls = rec.get("resource_urls")
    return bool(isinstance(urls, list) and urls)


def check_enriched_source_linkage(root: Path, rep: Report) -> None:
    """P3-A5: a record stamped enrichment_status=='enriched' must carry a resolvable source link.

    A record enriched from Wikipedia/Grokipedia/NARA/archive.org but with no stored link is
    un-attributable (we cannot show the user WHERE the fact came from) — a hard failure. Scoped to
    the descriptive-enrichment entities in ``_ENRICHED_ENTITIES`` (places/bibliography/weather are
    intentionally not gated here — see that map's comment).
    """
    for entity, glob in _ENRICHED_ENTITIES.items():
        for f in _entity_files(root, glob):
            rec = _load(f)
            if not isinstance(rec, dict):
                continue
            if str(rec.get("enrichment_status", "")).strip() != "enriched":
                continue
            if not _has_link(rec):
                rep.fail(
                    "P3-A5",
                    f"{entity}/{f.name}: enrichment_status=enriched but no source link "
                    f"(top-level/biographical_profile/external_data) — un-attributable enrichment",
                )


# --- P3-B4: coverage floors (WARN unless --strict-coverage) ------------------------------------
def _places_with_coords(root: Path) -> int:
    n = 0
    for f in _entity_files(root, "output/places/*.json"):
        rec = _load(f)
        if not isinstance(rec, dict):
            continue
        loc = rec.get("location") or rec.get("coordinates") or {}
        if isinstance(loc, dict):
            lat, lon = loc.get("latitude", 0.0), loc.get("longitude", 0.0)
            if (lat or lon) and not (lat == 0.0 and lon == 0.0):
                n += 1
    return n


def check_coverage_floors(root: Path, rep: Report, strict: bool) -> None:
    emit = rep.fail if strict else rep.warn
    total_places = len(_entity_files(root, "output/places/*.json"))
    if total_places > 0 and _places_with_coords(root) == 0:
        emit(
            "P3-B4",
            f"{total_places} places but 0 have coordinates after Phase 3 "
            "(geocoder cascade down / step skipped?)",
        )
    for entity, glob, marker in (
        ("bibliography", "output/bibliography/*.json", "isbn"),
        ("weather", "output/weather/*.json", "noaa_observed"),
    ):
        files = _entity_files(root, glob)
        if not files:
            continue
        enriched = sum(
            1 for f in files if isinstance((r := _load(f)), dict) and r.get(marker)
        )
        if enriched == 0:
            emit(
                "P3-B4",
                f"{len(files)} {entity} records but 0 carry '{marker}' "
                "(enrichment source down / not run?)",
            )


def run(output_root: str, strict_coverage: bool) -> int:
    root = Path(output_root)
    rep = Report()
    if not root.exists():
        print(
            f"OBSERV phase3.validation result=FAIL — output root {root} does not exist"
        )
        return 1

    checks = [
        ("A1/A2", lambda: check_schema_and_version(root, rep)),
        ("A3", lambda: check_source_traceback(root, rep)),
        ("A3/B1", lambda: check_referential_integrity(root, rep)),
        ("P3-A5", lambda: check_enriched_source_linkage(root, rep)),
        ("P3-B4", lambda: check_coverage_floors(root, rep, strict_coverage)),
    ]
    for label, fn in checks:
        try:
            fn()
        except (
            Exception
        ) as ex:  # noqa: BLE001 - a crashing check is itself a hard failure
            rep.fail(label, f"check crashed: {type(ex).__name__}: {ex}")

    result = "FAIL" if rep.failures else "PASS"
    print(
        f"OBSERV phase3.validation result={result} files_checked={rep.checks_run} "
        f"failures={len(rep.failures)} warnings={len(rep.warnings)}"
    )
    for w in rep.warnings:
        print(f"  WARN  {w}")
    for fail in rep.failures:
        print(f"  FAIL  {fail}")
    if rep.failures:
        print(f"=== PHASE3 VALIDATION FAIL: {len(rep.failures)} hard failure(s) ===")
        return 1
    print("=== PHASE3 VALIDATION PASS ===")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Post-Phase-3 data validation")
    p.add_argument(
        "--output-root", default="output", help="Local output/ tree to validate"
    )
    p.add_argument(
        "--strict-coverage",
        action="store_true",
        help="Promote P3-B4 0-of-N coverage warnings to HARD failures (default: warn)",
    )
    args = p.parse_args()
    return run(args.output_root, args.strict_coverage)


if __name__ == "__main__":
    sys.exit(main())
