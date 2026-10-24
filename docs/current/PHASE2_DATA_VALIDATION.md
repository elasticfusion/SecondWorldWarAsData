# Phase-2 Data Validation — Design & Checklist

**Status:** design (spec for a post-Phase-2 validation step). **Owner-driven.**
**Principle:** after a Phase-2 run completes, a single validation pass asserts the generated data is
**schema-correct, spec-compliant, fully traceable to source, and enriched as intended.** Every
failure is surfaced **LOUDLY** (a non-zero exit + an explicit `VALIDATION FAIL` summary + an SNS
data-quality alert), never a silent pass. This runs **post-Phase-2** (after the batch RETRIEVE pass
completes and all entities are finalized), reusing existing mechanisms rather than re-deriving them.

Scope: the entity data Phase 2 produces — `output/{people,places,people_groups,dates,equipment,
casualties,weather,logistics,maps,bibliography,source_section}` + `output/content/*/*-event.json`.

---

## A. Owner checklist (must-pass)

### A1. Do the schema versions match?
Every persisted record carries `_schema_version` (stamped by `inject_metadata` at write from
`ENTITY_SCHEMA_VERSIONS`). Validate:
- **Per record:** `_schema_version == entity_version(entity_type)` for its entity. A record stamped
  OLDER than target ⇒ a stale/upgrade-needed write (flag). A record stamped NEWER than target ⇒
  impossible/corrupt (already guarded by `_repair_schema_version`, but assert none slipped through).
- **Fingerprint parity:** the live schema shape still matches the pinned fingerprint
  (`tests/test_schema_fingerprint.py`) — catches an un-repinned schema shape change that would make
  `_schema_version` lie. (CI already enforces; the post-run check re-asserts against the deployed code.)
- **LOUD on:** any record whose `_schema_version` ≠ the entity target.

### A2. Do the schemas themselves comply with the spec (incl. mandatory fields)?
For every record, validate against its registry schema (`ENTITY_REGISTRY[*].schema_import`,
`additionalProperties: false`):
- **Mandatory/required fields present** (e.g. people `[PersonID, name]`, places `[PlaceID]`,
  bibliography `[BibliographyID, title]`, events `[Event]`).
- **No unknown/extra fields** (additionalProperties violation = the LLM invented a key).
- **Types / enums / ULID patterns** valid (PersonID/EventID etc. match the 26-char ULID pattern).
- **LOUD on:** any jsonschema error, missing required field, or extra field.
- *Note:* this is the SAME contract the write-guard (`_validate_entity`) enforces per-write; the
  post-run pass is a corpus-wide backstop that also catches records written before a schema change.

### A3. Can every JSON be tracked back to its source point?
Every entity must be traceable to the document it came from. Each entity's `event_mentions[]`
carries the provenance chain: `EventID`, `Sub_eventID`, `book`, `author`, `series`, `original_text`
(the verbatim quote), and the mention's own `MentionID`. Validate:
- **Every entity has ≥1 `event_mention`** (no orphan entity with no source).
- **Each mention resolves:** its `EventID`/`Sub_eventID` exist in the actual event key-space
  (`referential_integrity.audit` — resolves every cross-reference edge; dangling = a reference to
  an event that was never persisted). **Target: 0 dangling** (Phase-1 already logged `0 dangling`;
  Phase-2 must not introduce any).
- **`original_text` present + non-empty** on each mention (the verbatim anchor back to the page).
- **`book`/`series` present** so the entity maps to a real source document under `contentrepository/`.
- **LOUD on:** any entity with 0 mentions; any dangling EventID/Sub_eventID; any mention missing
  `original_text` or `book`.

### A4. Did Grokipedia/Wikipedia enhancement occur? (+ is it linked back to source?)
Phase-2 enriches **people** from Grokipedia/Wikipedia in place (moved from Phase 3). Validate:
- **Coverage:** the `enrich.summary` rollup (people `total=N enriched=M skipped_already=K
  collecting=J errors=E`) — assert **not** `total>0 && enriched==0 && collecting==0` (the
  `enrich.anomaly` case = enrichment dead, the exact bug that hid before).
- **Per obvious candidate:** a configurable sample of well-known people (e.g. named generals) SHOULD
  have `enrichment_status == "enriched"` OR a recorded `not_found` with `last_enrichment_search`
  (proving it was ATTEMPTED, not skipped). A famous person with neither is suspicious → flag.
- **Source linkage captured + traceable:** when enriched, the record must carry the **source link**
  — `wikipedia_url` and/or `grokipedia_url` — AND the biography content must cite it:
  `biographical_profile.biography_sources[]` present, each with its source URL. `wikipedia_checked_at`
  stamped (even on a miss, so it isn't re-fetched). So the enrichment data is **linkable back to the
  exact external source**, and the fetch itself is traceable via the **fetch-provenance** record
  (`provider=grokipedia|wikipedia`, endpoint, queried_at, outcome) in `output/_fetch_events/` /
  the DynamoDB `fetchevent#` store.
- **LOUD on:** `enrich.anomaly` (0-of-N); an enriched record with no `wikipedia_url`/`grokipedia_url`;
  an enriched record whose `biography_sources` is empty (enriched but un-attributable = data without
  provenance).

---

## B. Additional Phase-2 validations (surfaced per owner request)

### B1. Referential integrity (cross-entity)
Beyond A3's source linkage: every cross-entity reference resolves — a casualty's
`impacted_organizations[].PeopleGroupID`, a person's `units_served[].GroupID`, a place's
`related_places`, a weather mention's `PlaceMentionID`/`DateMentionID`. Use
`referential_integrity.audit` corpus-wide; **0 dangling** is the gate. (This caught the dedup
reference-rebase CRITICAL earlier — it's the right tool.)

### B2. Identity-ID invariants
- **PKs are server-minted ULIDs** (PersonID/PlaceID/…): every entity has exactly one, 26-char ULID,
  unique within its entity type (no collisions, no LLM-supplied value that leaked through).
- **Reference IDs are never minted** (EventID/Sub-eventID on a mention point at REAL events, not
  freshly-invented ULIDs) — assert each resolves (A3/B1).

### B3. No duplicate entities (dedup sanity)
- The per-entity `index.json` maps `normalized_name → filename` with no two distinct files claiming
  the same normalized identity (would indicate a missed dedup). Equipment/places also carry canonical
  keys — assert no obvious same-canonical duplicates slipped the extraction-time match.
- **LOUD on:** two files resolving to the same canonical identity (a split that dedup should catch).

### B4. Extraction-quality signals (already emitted — assert them)
Phase-2 already logs these; the validator asserts thresholds:
- **JSON quality:** `clean% == 100` (0 repaired/truncated/failed) — a repaired/truncated response is
  a soft data-quality risk.
- **Validation block rate:** `block_rate == 0%` (any blocked write = a record the guard rejected).
- **Systematic `allow_flagged` clusters:** `validation_stats` flags e.g.
  `places/geo_missing_coords ≥ threshold` as "likely a CODE defect — reprocess cannot fix." Surface
  these as **warnings** (not hard fails — some are expected pre-Phase-3, e.g. null coords awaiting
  geocoding), but list them explicitly so a NEW systematic cluster is visible.

### B5. Completeness / non-empty expectations
- A book that parsed N chapters in Phase 1 must yield ≥1 event file and a non-trivial entity count
  (0 people/places from a real WWII chapter = extraction silently failed). Compare against the
  Phase-1 `run_scope` section_count.
- **LOUD on:** an event with 0 extracted entities of every type (likely a failed sub-event).

### B6. Deterministic / idempotency sanity
- No unbounded re-append duplication (the PATTERN-4 class): mention lists deduped by
  `Sub_eventID`/`MentionID`; a re-run must not grow `event_mentions` without new sources.

### B7. Encoding / verbatim fidelity
- `original_text` and name fields preserve UTF-8 / diacritics (e.g. "Hürtgen" not "H?rtgen",
  "Hürtgen" not mojibake) — a mangled verbatim quote breaks traceability and search.

### B8. Fetch-provenance completeness (for anything fetched this run)
- Every external fetch in the run (geocode/weather/wikipedia/grokipedia/NARA/archive/…) emitted a
  fetch-provenance event (provider/endpoint/queried_at/http_status/outcome). A result with no
  corresponding fetch-event = an untracked side-channel (the thing the fetch-provenance work closed).

---

## C. How failures are surfaced (LOUD)

1. **Exit code:** the validator exits non-zero on any **A-level** failure (schema/version/traceback/
   enrichment-anomaly) → fails the pipeline step / CI.
2. **Explicit summary line:** `OBSERV phase2.validation result=FAIL checks=N failed=M` with a
   per-check breakdown; `result=PASS` only when all A-checks pass.
3. **SNS data-quality alert:** reuse the existing `validation_stats` systematic-failure alert channel
   so a FAIL pages the operator (same path that already alerts on `geo_missing_coords`).
4. **Per-failure detail:** entity type + file + the specific violation (schema path, missing field,
   dangling ID value, un-attributed enrichment) — enough to act without re-deriving.
5. **Warnings (non-fatal) are listed but do not fail:** B4 systematic clusters that are expected
   pre-Phase-3 (e.g. null coords), B7 edge cases — visible, not blocking.

---

## D. Reuse (don't re-derive)
- Schema validation + required/additionalProperties: the registry schemas + the SAME contract as
  `_validate_entity`.
- Versions: `ENTITY_SCHEMA_VERSIONS` / `entity_version`; fingerprint: `test_schema_fingerprint`.
- Cross-refs / traceback: `referential_integrity.audit` (read-only, corpus-wide).
- Enrichment coverage: the `enrich.summary` / `enrich.anomaly` observability.
- Extraction quality: `validation_stats` (block rate, JSON quality, systematic clusters).
- Fetch traceability: the fetch-provenance store (`output/_fetch_events/` + `fetchevent#`).

## E. Open questions (resolve before implementing the step)
1. Which checks are **hard-fail (A)** vs **warn (B)** — confirm the A/B split above.
2. The "obvious candidate" enrichment sample (A4): a curated allowlist of expected-enriched names,
   or a heuristic (rank ≥ general / appears in N sub-events)? 
3. Pre-Phase-3 expectations: null place coords + absent weather/source_section (pending batch) must
   be WARN, not FAIL, when Phase 2 ran standalone.
4. Where the step runs: a new `scripts/validate_phase2.py` invoked at the end of `phase2_extract.py`
   (ECS) + runnable standalone against a downloaded `output/`.
