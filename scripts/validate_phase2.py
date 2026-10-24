#!/usr/bin/env python3
"""Post-Phase-2 data validation (spec: docs/current/PHASE2_DATA_VALIDATION.md).

Runs AFTER Phase 2 finishes and asserts the generated entity data is schema-correct,
spec-compliant, traceable to source, and enriched as intended. A-level failures are HARD
(non-zero exit + ``VALIDATION FAIL``); B-level issues are WARNINGS (listed, non-fatal).

Reuses existing mechanisms (no re-derivation):
  • schemas + versions : ENTITY_REGISTRY schema_import / ENTITY_SCHEMA_VERSIONS / entity_version
  • cross-refs         : referential_integrity.audit  (0 dangling gate)
  • storage            : LocalStorage (runs against a local ``output/`` tree)

Usage:
  python scripts/validate_phase2.py [--output-root output] [--strict-enrichment]
    --strict-enrichment : treat A4 enrichment-anomaly (0-of-N people enriched) as HARD fail
                          (default: WARN, because a standalone Phase-1+2 run may defer people
                          enrichment to a pending batch — see the spec's pre-Phase-3 note).
Exit: 0 = PASS (no A failures); 1 = FAIL (≥1 A failure).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import jsonschema

# --- reuse real mechanisms ---------------------------------------------------------------------
from src.schemas import ENTITY_SCHEMA_VERSIONS, entity_version
from src.schemas import entity_registry as _er
from src.utils.storage import LocalStorage

# entity dir name -> glob leaf under output/ (events live nested under content/)
_SKIP_NAMES = {
    "index.json",
    ".processed_events.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "related_groups_report.json",
    "surname_frequency_suggestions.json",
    "not_people.json",
}


class Report:
    """Accumulates A-level failures (hard) and B-level warnings (soft)."""

    def __init__(self) -> None:
        self.failures: List[str] = []  # A — hard, non-zero exit
        self.warnings: List[str] = []  # B — soft
        self.checks_run = 0

    def fail(self, check: str, detail: str) -> None:
        self.failures.append(f"[{check}] {detail}")

    def warn(self, check: str, detail: str) -> None:
        self.warnings.append(f"[{check}] {detail}")


def _resolve_schema(spec: str) -> Dict[str, Any]:
    mod, attr = spec.split(":")
    return getattr(importlib.import_module(mod), attr)


def _entity_files(root: Path, glob: str) -> List[Path]:
    files = [
        f
        for f in root.glob(glob.replace("output/", "", 1))
        if not f.name.endswith(".lock") and f.name not in _SKIP_NAMES
    ]
    return files


def _load(f: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _iter_mentions(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    ems = rec.get("event_mentions")
    return ems if isinstance(ems, list) else []


# --- A1 + A2: schema version match + schema/mandatory-field compliance -------------------------
def check_schema_and_version(root: Path, rep: Report) -> None:
    for e in _er.ENTITY_REGISTRY:
        try:
            schema = _resolve_schema(e.schema_import)
        except Exception as ex:  # noqa: BLE001
            rep.fail("A2", f"{e.name}: cannot resolve schema ({ex})")
            continue
        files = _entity_files(root, e.output_glob)
        if not files:
            continue
        validator = jsonschema.Draft7Validator(schema)
        target_ver = ENTITY_SCHEMA_VERSIONS.get(e.name)
        for f in files:
            rep.checks_run += 1
            _check_one_record(e.name, f, validator, target_ver, rep)


def _check_one_record(entity, f, validator, target_ver, rep) -> None:
    """A1 (version match) + A2 (schema/required/additionalProperties) for one record."""
    rec = _load(f)
    if rec is None:
        rep.fail("A2", f"{entity}/{f.name}: unreadable JSON")
        return
    sv = str(rec.get("_schema_version", "")).strip()
    if target_ver and not sv:
        rep.fail(
            "A1",
            f"{entity}/{f.name}: missing _schema_version (expected {target_ver!r})",
        )
    elif target_ver and sv != target_ver:
        rep.fail(
            "A1", f"{entity}/{f.name}: _schema_version={sv!r} != target {target_ver!r}"
        )
    for err in sorted(validator.iter_errors(rec), key=lambda x: list(x.path))[:3]:
        loc = "/".join(map(str, err.path)) or "(root)"
        rep.fail("A2", f"{entity}/{f.name} @ {loc}: {err.message[:120]}")


# --- A3: source traceback ----------------------------------------------------------------------
# Entity types whose records are source-anchored via event_mentions (not every type: a map/image
# may anchor differently). People/places/people_groups/equipment/dates carry event_mentions.
_MENTION_ENTITIES = {
    "people",
    "places",
    "people_groups",
    "equipment",
    "dates",
    "casualties",
}


def check_source_traceback(root: Path, rep: Report) -> None:
    for e in _er.ENTITY_REGISTRY:
        if e.name not in _MENTION_ENTITIES:
            continue
        for f in _entity_files(root, e.output_glob):
            rec = _load(f)
            if rec is not None:
                _check_record_mentions(e.name, f, rec, rep)


def _check_record_mentions(entity, f, rec, rep: Report) -> None:
    """A3 per-record: ≥1 event_mention, each a dict with an EventID (hard); original_text/book (warn)."""
    mentions = _iter_mentions(rec)
    if not mentions:
        rep.fail("A3", f"{entity}/{f.name}: no event_mentions (orphan — no source)")
        return
    for m in mentions:
        if not isinstance(m, dict):
            rep.fail("A3", f"{entity}/{f.name}: non-dict event_mention element")
            continue
        if not str(m.get("EventID", "")).strip():
            rep.fail("A3", f"{entity}/{f.name}: a mention has no EventID")
        if not str(m.get("original_text", "")).strip():
            rep.warn("A3", f"{entity}/{f.name}: a mention has empty original_text")
        if not str(m.get("book", "")).strip():
            rep.warn("A3", f"{entity}/{f.name}: a mention has no book")


def check_referential_integrity(root: Path, rep: Report) -> None:
    try:
        from src.utils.referential_integrity import audit

        result = audit(LocalStorage(base_dir=root))
    except Exception as ex:  # noqa: BLE001
        # This is a HARD check (0 dangling cross-refs). If the audit itself can't run, we must NOT
        # report PASS — a skipped integrity check is a validation failure, not a warning (unlike the
        # in-pipeline enforcer, which fail-safes to not crash a phase; the standalone validator's
        # mandate is loud).
        rep.fail("A3/B1", f"referential-integrity audit could not run: {ex}")
        return
    total = result.get("total_dangling", 0)
    if total:
        worst = [ed for ed in result.get("edges", []) if ed.get("dangling")]
        detail = ", ".join(
            f"{ed.get('edge', '?')}={ed['dangling']}" for ed in worst[:5]
        )
        rep.fail("A3/B1", f"{total} dangling cross-reference(s): {detail}")


# --- A4: enrichment coverage + source linkage --------------------------------------------------
def _enrichment_signals(rec: Dict[str, Any]) -> Tuple[bool, bool, list]:
    """(is_enriched, has_source_link, biography_sources) for a person record."""
    bp = rec.get("biographical_profile")
    bp = bp if isinstance(bp, dict) else {}
    links = (
        rec.get("wikipedia_url"),
        rec.get("grokipedia_url"),
        bp.get("wikipedia_url"),
        bp.get("grokipedia_url"),
    )
    has_link = any(links)
    sources = bp.get("biography_sources") or []
    is_enriched = bool(
        rec.get("enrichment_status") == "enriched" or has_link or sources
    )
    return is_enriched, has_link, sources


def _check_one_person_enrichment(f: Path, rec: Dict[str, Any], rep: Report) -> bool:
    """Validate one person's enrichment linkage; return True if the record is enriched."""
    is_enriched, has_link, sources = _enrichment_signals(rec)
    if not is_enriched:
        return False
    if not has_link:
        rep.fail(
            "A4",
            f"people/{f.name}: enriched but no wikipedia_url/grokipedia_url (un-linkable)",
        )
    if not sources:
        rep.warn(
            "A4",
            f"people/{f.name}: enriched but biography_sources empty (un-attributed)",
        )
    return True


def check_people_enrichment(root: Path, rep: Report, strict: bool) -> None:
    people = _entity_files(root, "output/people/*.json")
    if not people:
        return
    total = enriched = 0
    for f in people:
        rec = _load(f)
        if rec is None:
            continue
        total += 1
        if _check_one_person_enrichment(f, rec, rep):
            enriched += 1
    if total > 0 and enriched == 0:
        msg = f"{total} people but 0 enriched (Grokipedia/Wikipedia enrichment did not occur)"
        (rep.fail if strict else rep.warn)("A4", msg)


# --- B3: dedup sanity (index name->filename, no duplicate canonical identity) ------------------
def check_dedup_indexes(root: Path, rep: Report) -> None:
    for name in ("people", "places", "people_groups", "equipment", "dates"):
        idx = root / name / "index.json"
        if not idx.exists():
            continue
        data = _load(idx)
        if not isinstance(data, dict):
            continue
        seen: Dict[str, str] = {}
        for key, filename in data.items():
            if filename in seen and seen[filename] != key:
                # two distinct normalized names -> same file is fine (aliases);
                # two names mapping to DIFFERENT files for the same identity is the smell — but the
                # index is name->file, so flag only a name pointing at a missing file.
                pass
            if not (root / name / str(filename)).exists():
                rep.warn("B3", f"{name}/index.json: '{key}' -> missing file {filename}")
            seen[filename] = key


def run(output_root: str, strict_enrichment: bool) -> int:
    root = Path(output_root)
    rep = Report()
    if not root.exists():
        print(
            f"OBSERV phase2.validation result=FAIL — output root {root} does not exist"
        )
        return 1

    # Each check is dispatched fail-safe: an unexpected exception becomes a HARD failure (never a
    # silent crash) and does NOT abort the remaining checks or the LOUD summary.
    checks = [
        ("A1/A2", lambda: check_schema_and_version(root, rep)),
        ("A3", lambda: check_source_traceback(root, rep)),
        ("A3/B1", lambda: check_referential_integrity(root, rep)),
        ("A4", lambda: check_people_enrichment(root, rep, strict_enrichment)),
        ("B3", lambda: check_dedup_indexes(root, rep)),
    ]
    for label, fn in checks:
        try:
            fn()
        except (
            Exception
        ) as ex:  # noqa: BLE001 - a crashing check is itself a hard failure
            rep.fail(label, f"check crashed: {type(ex).__name__}: {ex}")

    # LOUD summary
    result = "FAIL" if rep.failures else "PASS"
    print(
        f"OBSERV phase2.validation result={result} files_checked={rep.checks_run} "
        f"failures={len(rep.failures)} warnings={len(rep.warnings)}"
    )
    for w in rep.warnings:
        print(f"  WARN  {w}")
    for fail in rep.failures:
        print(f"  FAIL  {fail}")
    if rep.failures:
        print(f"=== PHASE2 VALIDATION FAIL: {len(rep.failures)} hard failure(s) ===")
        return 1
    print("=== PHASE2 VALIDATION PASS ===")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Post-Phase-2 data validation")
    p.add_argument(
        "--output-root", default="output", help="Local output/ tree to validate"
    )
    p.add_argument(
        "--strict-enrichment",
        action="store_true",
        help="Treat the A4 0-of-N people-enrichment anomaly as a HARD fail (default: warn)",
    )
    args = p.parse_args()
    return run(args.output_root, args.strict_enrichment)


if __name__ == "__main__":
    sys.exit(main())
