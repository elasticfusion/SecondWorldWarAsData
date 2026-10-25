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
import jsonschema

from scripts.validate_phase2 import (  # type: ignore[import-not-found]
    Report,
    _entity_files,
    _iter_mentions,
    _load,
    _resolve_schema,
    check_referential_integrity,
    check_schema_and_version,
    check_source_traceback,
)
from src.schemas import ENTITY_SCHEMA_VERSIONS
from src.schemas import entity_registry as _er


# entity name -> (schema dict, target version) for the P3-A5 full-compliance check. Built lazily
# from the registry so it can never drift from A1/A2.
def _entity_schema(entity: str):
    for e in _er.ENTITY_REGISTRY:
        if e.name == entity:
            try:
                return _resolve_schema(e.schema_import), ENTITY_SCHEMA_VERSIONS.get(
                    entity
                )
            except Exception:  # noqa: BLE001
                return None, ENTITY_SCHEMA_VERSIONS.get(entity)
    return None, None


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
    if isinstance(urls, list) and urls:
        return True
    # Structured-facts enrichment (e.g. groups via the Grok group_enrichment prompt) records its
    # provenance under enrichment_data._source = {type, query, retrieved_at} rather than a
    # descriptive top-level URL. A populated _source IS traceable attribution.
    ed = rec.get("enrichment_data")
    if isinstance(ed, dict):
        src = ed.get("_source")
        if isinstance(src, dict) and src.get("type"):
            return True
    return False


def _check_enriched_record_compliance(entity, f, rec, schema, target_ver, rep) -> None:
    """P3-A5 full compliance for ONE enriched record — the same rigor as A1/A2/A3, asserted at the
    point of enrichment so an enrichment that corrupted/under-populated the record is caught here:
      • schema-valid + mandatory fields (jsonschema required + additionalProperties),
      • version-matched (_schema_version == target),
      • source-linked (attribution: a resolvable wiki/grok/archive/source URL),
      • origin-traceable (>=1 event_mention carrying an EventID back to the source event).
    """
    # (a) source link — the enrichment must be attributable.
    if not _has_link(rec):
        rep.fail(
            "P3-A5",
            f"{entity}/{f.name}: enrichment_status=enriched but no source link "
            f"(top-level/biographical_profile/external_data) — un-attributable enrichment",
        )
    # (b) schema + mandatory-field validity of the enriched record.
    if schema is not None:
        for err in sorted(
            jsonschema.Draft7Validator(schema).iter_errors(rec),
            key=lambda x: list(x.path),
        )[:3]:
            loc = "/".join(map(str, err.path)) or "(root)"
            rep.fail(
                "P3-A5",
                f"{entity}/{f.name} @ {loc}: enriched record schema-invalid: {err.message[:110]}",
            )
    # (c) schema-version match.
    sv = str(rec.get("_schema_version", "")).strip()
    if target_ver and sv != target_ver:
        rep.fail(
            "P3-A5",
            f"{entity}/{f.name}: enriched record _schema_version={sv!r} != target {target_ver!r}",
        )
    # (d) traceability back to the original source event(s): >=1 event_mention with an EventID.
    mentions = _iter_mentions(rec)
    if not mentions:
        rep.fail(
            "P3-A5",
            f"{entity}/{f.name}: enriched but no event_mentions — enrichment not traceable to source",
        )
    elif not any(
        isinstance(m, dict) and str(m.get("EventID", "")).strip() for m in mentions
    ):
        rep.fail(
            "P3-A5",
            f"{entity}/{f.name}: enriched but no event_mention carries an EventID (untraceable)",
        )


def check_enriched_source_linkage(root: Path, rep: Report) -> None:
    """P3-A5: every record stamped enrichment_status=='enriched' (people/people_groups/equipment)
    must be fully compliant — schema-valid + mandatory-complete + version-matched + source-linked +
    traceable to its origin event(s). This gives enriched records the SAME rigor A1/A2/A3 apply to
    the corpus, asserted specifically where enrichment mutated the record. (places/bibliography/
    weather are not gated here — see _ENRICHED_ENTITIES — but are still covered by A1/A2/A3 +
    the A3/B1 referential audit, which resolves EventID AND Sub_eventID edges to the events.)
    """
    for entity, glob in _ENRICHED_ENTITIES.items():
        schema, target_ver = _entity_schema(entity)
        for f in _entity_files(root, glob):
            rec = _load(f)
            if not isinstance(rec, dict):
                continue
            if str(rec.get("enrichment_status", "")).strip() != "enriched":
                continue
            _check_enriched_record_compliance(entity, f, rec, schema, target_ver, rep)


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
