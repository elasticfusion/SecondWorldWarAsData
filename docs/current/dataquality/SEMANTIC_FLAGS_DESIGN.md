# Semantic Quality Flags — Design (HIGH-2)

**Status:** BUILT (proposed 2026-10-10; verified against the code 2026-11-16). Non-blocking semantic validators that FLAG (never reject) records
whose content is structurally valid but semantically implausible. Complements the write guard
(shape) and the referential checker (cross-refs) — this layer attacks CONTENT validity.

## Principles (inherited)
- **Flag, never block / never fabricate.** A low-plausibility value is annotated, not dropped or
  "corrected". The record still writes.
- **Centralized.** One module, one flag vocabulary, applied consistently; reusable by the write
  path, the catcher, and the (future) corpus-quality report.
- **Fail-safe.** A validator error must never affect the write.
- **Observable.** Flags are additive data a human/dashboard can filter on; they also feed the
  statistical catcher (systematic clusters → code fix), not per-record adjudication.

## Where the flags live
A reserved, additive field `quality_flags: [string]` on the record (list of flag CODES, e.g.
`"geo_impossible_coords"`). Absent/empty = no concern. Additive + optional → no breaking schema change
(no version bump needed if declared as optional; see Rollout). NEVER overwrites content fields.

## The three validators (v1)

### 1. Geocode plausibility (places) — GLOBAL
Fields: the canonical nested `coordinates.latitude/longitude`, then the root `latitude`/`longitude`
(the Phase-3 mirror), read by `semantic_flags.place_latlon` (the first complete, non-placeholder pair
wins; reading only the root once flagged EVERY Phase-2 place `geo_missing_coords`). WWII was a GLOBAL conflict and the corpus
will grow worldwide, so there is deliberately **NO theater/region bounding box** — a regional box
would flag legitimate places by design as coverage expands. We validate only coordinate validity:
- `geo_missing_coords` — lat/lon null OR both exactly 0.0 (the placeholder). (Geocoding is a Phase-3
  pass, so freshly extracted places carry this flag until then.)
- `geo_impossible_coords` — lat outside −90..90 or lon outside −180..180 (a garbage geocode, e.g.
  "Channel coast" → −127.6 British Columbia). A plausible-but-far-from-Europe coordinate (Pacific,
  Washington, Moscow) is CORRECT and NOT flagged.

### 2. Date-in-window (dates)
Fields: `resolved_earliest`, `resolved_latest` (ISO), fallback `date_start`/`normalized_datetime`.
- `date_out_of_window` — a resolved date falls outside the configured date window. This is a WWII
  DATA corpus (not ETO-only) with a long antecedent/aftermath tail, so the window is GENEROUS and
  CONFIGURABLE, never a hardcoded single-theater span. Default **1930-01-01 … 1950-12-31** so it
  does NOT false-flag legitimate antecedents the corpus covers — Italian invasion of Abyssinia
  (1935), Spanish Civil War (1936–39), Second Sino-Japanese War (1937), Anschluss/Sudetenland
  (1938), Khalkhin Gol (1939) — nor the late-1940s aftermath (occupation, trials, demobilization).
  Override per corpus via config `processing.date_window: {start, end}` or env `DATE_WINDOW_START` /
  `DATE_WINDOW_END` (ISO).
- `date_unparseable` — a non-null date string that does not parse to ISO (approximate forms like
  `summer-1944` are NOT flagged — they legitimately resolve to a range).

### 3. Citation resolvability (bibliography)
Fields: `archive_reference_number`, `resource_urls`, `citation`, `title`.
- `citation_stub` — citation/title is an endnote fragment (`ibid`, `op. cit.`, a bare number, empty)
  → cannot resolve to a real source.
- `citation_unresolvable` — a POST-RESOLUTION signal: raised only once a resolver has set
  `search_status` AND the record has NO identity at all (no `archive_reference_number`, no
  `resource_urls`, no `source_metadata.canonical_title`, and `search_status` is not
  `reference_identified`). A freshly extracted citation (no `search_status`) is merely not yet
  resolved and is NOT flagged (flagging it produced a ~50% false-positive rate and a
  data-quality alert). (URL liveness is NOT checked here — no network in
  the flag pass; a separate enrichment step dereferences.)

## API (central module `src/utils/semantic_flags.py`)
- `flags_for(entity: str, record: dict) -> list[str]` — pure, returns the flag codes (no mutation).
- `annotate(entity: str, record: dict) -> dict` — sets `record["quality_flags"]` (union, dedup);
  fail-safe (returns record unchanged on any error).
- Constants: `_LAT_MIN/_LAT_MAX/_LON_MIN/_LON_MAX` (global validity), `DATE_WINDOW_START`,
  `DATE_WINDOW_END` (env > config `processing.date_window` > default), `_CITATION_STUBS`,
  `_OWNED_FLAGS`.

## Where it plugs in
Called at write time for the three entities (`file_lock._annotate_semantic_flags`, used by the
central write guard `_validate_entity`), AFTER
PK repair, BEFORE schema validation — additive only, never changes the did-write result. `annotate`
RECOMPUTES + REPLACES this module's own flag codes each run (so a flag CLEARS once the problem is
fixed — e.g. a later-geocoded place loses `geo_missing_coords`), preserving any foreign flags.
Flags ARE forwarded to the statistical catcher: `validation_stats.record_record_quality` records an
`allow_flagged` datapoint per flag code (see `CATCHER_LOW_INFO_FLAGGED_DESIGN.md`). The per-mention merge path (`locked_json`/`save()`)
is annotated too so coordinate-less creates are covered.

`RESOLVED_IN_PHASE3 = {geo_missing_coords}` (`semantic_flags.py:213`): Phase 2 writes places without
coordinates by design, so `phase2_extract.py` passes this set as `deferred_flags` to
`write_validation_stats` and an `allow_flagged geo_missing_coords` cluster does not alert at the end of
Phase 2 (it is still counted in `output/metrics/validation_stats.json`). `phase3_enrich_data.py` passes
no deferral, so the same flag alerts after Phase 3.

## Expected base rates (calibrate the catcher, not false alarms)
- `date_out_of_window`: with the generous default (1930–1950) only genuinely implausible dates fire
  (OCR errors, mis-parsed years) — WWII antecedents (1935–39) and late-1940s aftermath are IN-window
  by design. Narrow the window per corpus (config/env) only if you want a tighter campaign check,
  accepting the higher expected rate that implies.
- `geo_impossible_coords`: expected to be rare (only physically-invalid garbage geocodes).
  There is NO theater box — a place far from Europe (Pacific, US, USSR) is legitimate for a global
  conflict and is never flagged. The main geo signal for a Phase-2-only place is `geo_missing_coords` (no geocode until Phase 3).

## Rollout / schema
`quality_flags` is additive + optional: declared `[array,null] of string` in the shared field block of
`src/schemas/__init__.py`. The fingerprint guard requires a version bump for each touched schema.

## Explicitly out of scope (v1)
- URL liveness / network dereference (separate enrichment).
- Auto-correction of bad coords/dates (violates flag-not-fabricate).
- Cross-source corroboration (LOW backlog item).

## Testing
- Pure `flags_for`: valid worldwide coords vs impossible vs 0.0 placeholder; in-window vs 1946 date vs approximate form;
  real citation vs `ibid`/bare-number vs no-url-no-archive.
- `annotate` is additive, idempotent (re-annotate → same set), fail-safe on malformed input.
- Flags never alter content fields; never affect the did-write boolean.
