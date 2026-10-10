# Catcher: low-information + flagged ALLOWs (HIGH-4)

**Status:** proposed (2026-10-10). Make the statistical catcher SEE the quality problems that
currently hide behind a plain `allow`: records that are structurally valid but low-information
(empty biographies, no-coordinate places) or semantically flagged (HIGH-2 `quality_flags`).

## Problem
The catcher clusters write-guard outcomes `allow` / `block` / `allow_warn`. Its blind spot: a
record that validates but is useless (empty `{}` biography, 0.0/0.0 place) is counted as a clean
`allow`, so a SYSTEMATIC content-quality defect is invisible. HIGH-2 added semantic `quality_flags`
to records but did NOT forward them to the catcher — so flags sit on records, unaggregated.

## Design (additive to `src/utils/validation_stats.py` — one catcher, one vocabulary)

### New outcomes
- `OUTCOME_EMPTY = "allow_empty"` — the write proceeded, but the record's POPULATED-field count is
  below a per-entity floor (low information). `keyword` = `"below_floor"`.
- `OUTCOME_FLAGGED = "allow_flagged"` — the write proceeded, but the record carries one or more
  semantic `quality_flags`; recorded ONE datapoint per flag code with `keyword` = the flag code
  (e.g. `geo_missing_coords`), so clusters are per-flag.

Both are ALLOW-family (the record still wrote — flag/observe, never block). They are mutually
compatible with the plain `allow` already recorded (they are additional observations, so the guard
records `allow` AND, when applicable, `allow_empty` / `allow_flagged`).

### Generalized systematic detection
`systematic_clusters()` currently scans only `block`. Generalize to a CONCERN set
`{block, allow_empty, allow_flagged}` with the same count≥10 AND rate≥5%-of-entity thresholds, so a
book-wide `geo_missing_coords` or an empty-biography cluster raises a code-level alert exactly like
a block cluster. Each cluster row keeps `outcome` so the alert says what kind.

### Low-information floor (per entity)
A small, central table of minimum populated-field counts per entity (content fields that signal the
record carries real information — not just its PK + metadata). Default floor is conservative; only
entities with a defined floor are checked (others never emit `allow_empty`). Counting ignores
`_schema_version`/`_last_updated`/the PK/`quality_flags` and treats null/""/[]/{}/0.0-coords as
unpopulated.

## Wiring (central write guard)
In `_validate_entity`, AFTER a successful allow (and respecting the existing `record_stats` flag so
pre-checks don't double-count):
- compute populated-field count → if below the entity floor, `record_validation(entity,
  "allow_empty", "below_floor", version_state)`.
- for each code in `record["quality_flags"]` → `record_validation(entity, "allow_flagged", code,
  version_state)`.
Fail-safe: telemetry never breaks the write (the existing `record_validation` already swallows).

## Alerting
The existing phase-end path (`get_stats` + `systematic_clusters` → SNS) now naturally includes the
new clusters (generalized detection). No new alert channel.

## Testing
- `allow_empty` fires below floor, not at/above; floor table respected; unpopulated detection
  (null/empty/0.0 coords) correct.
- `allow_flagged` records one datapoint per flag code.
- `systematic_clusters` surfaces block AND empty AND flagged clusters at the thresholds; a
  below-threshold flagged set does not alert.
- guard integration: a flagged/empty record still WRITES (allow), and the outcomes are recorded
  once (no double-count under the pre-check path).
