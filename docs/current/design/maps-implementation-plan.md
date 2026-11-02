# Maps Refactor — Sequenced Implementation Plan

**Status:** PLAN · 2026-11-02 · implements `maps-target-architecture.md` (owner-approved) ·
grounded in a code inventory of the existing map/enrich/fetch/dedup libraries.
**Governing constraint (owner):** REUSE existing libraries; do NOT create parallel code doing the
same thing. Every step below says explicitly what to reuse / consolidate / retire.

Each step is independently shippable via the standing workflow (gate → dual review → CI → merge).
Ordered low-risk/foundational first.

---

## Existing libraries to REUSE (do not reinvent)

| Need | Reuse | Notes |
|---|---|---|
| SSRF-safe image/URL download | `src/utils/url_fetch.py:fetch()` | central, redirect-safe. **Consolidate** the 3 ad-hoc map downloaders onto it. |
| find-or-create entity + append reference | `src/extraction/places.py:_find_or_create_place` | GENERALIZE the pattern to organizations (people_groups has only `_save_group`). |
| Wikipedia/Grokipedia enrichment | `enrich_biographies` / `source_section_articles` (per-entity enrichers) | call for the "enrich-if-new" step; do not write new enrichers. |
| entity name index / lookup | `src/utils/entity_index.py` | for find-or-create lookups. |
| dedup/merge | `src/dedup/*` (merge, incremental, resolved_set) | for map + fed-back entity dedup. |
| map registration (extent linking) | `src/extraction/map_registration.py` | EXTEND (add org dimension), don't replace. |
| map-as-source render | `src/extraction/map_source_adapter.py` | reuse as-is in Phase 3. |
| web map search + vision | `openserp_maps.py`, `grok_search_maps.py` | KEEP as the separate "no map in source" feature; untouched. |

**Duplication to COLLAPSE (the owner's "multiple sources doing the same thing"):**
- 3 download utils → `grok_search_maps.download_image`, `maps.py:_download_map_image`,
  `openserp_maps.py:_download_map_image` → **one** shared fetch (`url_fetch.fetch`).
- 3 `create_map_record` builders → `maps.py`, `external_maps.py`, `openserp_maps.py` → **one**
  canonical map-record builder in a single module.

---

## Step 1 — Phase 1: populate `maps[]` from the anchor form + minimal reference footprint
**Goal:** the parser's map channel actually contains the maps (today `maps[]`=0; maps only in
`images[]` via #672).
- EXTEND `src/parser.py:extract_maps` to also recognize the raw-HTML `<a href="...map...jpg">`
  anchor form (REUSE the #672 `_extract_html_anchor_images` matching logic — factor the shared
  anchor-matcher so parser maps + images share ONE matcher, not two). Hi-res href preferred.
- Capture the in-document **reference footprint** per map: `map_id → [{paragraph_numbers (MANDATORY),
  Sub-eventID (when relevant), verbatim mention text}]` (OI3).
- Phase 1 writes the **bare-minimum Map stub JSON** (MapID + hi-res source_url + figure/map_id +
  reference footprint + source provenance). No download/classify/link.
- Single source of truth = `maps[]`.
- Tests: ch-8 → 11 maps in `maps[]` (hi-res), each with paragraph_numbers.
- Risk: low (parser-only, additive). Ships first.

## Step 2 — Consolidate the map record builder + downloader (no behavior change)
**Goal:** remove duplication BEFORE moving logic, so Phase 3 builds on one implementation.
- Create ONE canonical `create_map_record(...)` (move/merge the 3 builders into a single module,
  e.g. keep `maps.py`'s as the base; delete the `external_maps`/`openserp_maps` copies, repoint
  callers).
- Route ALL map image downloads through `url_fetch.fetch()`; delete the 3 ad-hoc downloaders.
- Pure refactor: identical outputs, fewer code paths. Tests: existing map tests still pass.
- Risk: medium (touches 3 modules); mitigated by "no behavior change" + tests.

## Step 3 — Map schema: add Organization link (+ version bump)
- Add `PeopleGroupID` (+ optional `PeopleGroupMentionID`) nullable ULID to
  `src/schemas/maps_output.py`. Full schema-versioning workflow (maps 2.24 → 2.25: fingerprint pin,
  history snapshot, changelog, stale-fixture grep).
- Risk: low (additive/nullable).

## Step 4 — Generalize find-or-create to organizations
**Goal:** the OI2 feedback mechanism needs find-or-create for people_groups (places already has it).
- Factor `places.py:_find_or_create_place` into a shared find-or-create-stub + append-reference +
  enrich-if-new helper; add the people_groups/organization variant (people_groups currently only
  has `_save_group`). REUSE `entity_index` for lookup, `enrich_biographies`/`source_section_articles`
  for the enrich-if-new step, `dedup` for collision. Provenance append carries paragraph_numbers
  (OI3).
- Only NEW entities enrich (OI1); existing → append reference only. Self-terminating (OI2).
- Risk: medium; covered by tests mirroring the places find-or-create tests.

## Step 5 — Phase 3: move map ANALYSIS here (download/classify/link/register)
- In Phase 3, consume the Phase-1 Map stubs: download (Step 2 util) → classify (`_classify_map_type`
  keyword + the tiered Grok-vision backstop for AMBIGUOUS only, batched + idempotent) → link
  **Event/Place/Date/Organization** → `map_registration` (EXTENDED with org dimension, Step 3
  field) → `map_source_adapter`.
- Idempotency: persist vision verdict + `map_vision_checked_at`; a re-run over an already-processed
  book issues ZERO new vision calls (assert via OBSERV) — closes the standing TODO.
- Reuse the find-or-create (Step 4) when analysis mints a new place/org.
- Risk: medium-high (the substantive move); gated by the earlier steps.

## Step 6 — Eliminate Phase-2 maps
- Remove the `_extract_maps` call from `phase2_extract.py`; retire `maps.py`'s finder path (its
  record builder/download already consolidated in Step 2 and reused by Phase 3).
- Retire the now-dead markdown-`[Map X]` finders once Step 1/5 cover the real forms.
- Risk: low once Phase 3 owns maps; verify no other caller.

## Step 7 — SQS: Phase-3 → Phase-2 feedback interface (net-new) + Map → Phase-3 re-entry
- Map→Phase-3 re-entry: CONFIRM a new Map entity publishes to the existing `EntityCreatedTopic`
  (→ Phase 3 via `trigger_handler`); wire if missing.
- Phase3→Phase2 feedback (NET-NEW): add a route so a NEW place/org (OI1) created in Phase 3 causes a
  SCOPED Phase-2 re-run. REUSE the existing SNS→`TriggerQueue`→`trigger_handler` machinery (add a
  Phase-2-targeted subscription/`pending#phase2#<book>` + a `trigger_handler` branch) — do NOT build
  a parallel queue. Self-terminating by OI2 (find-or-create → no new entity → no further feedback).
- Risk: high (infra + loop semantics); ship last, after the find-or-create convergence is proven.

---

## Observability (HIGH PRIORITY — first-class acceptance criterion, every step)

Per the governing principles (anti-swallow + ALL ALARMS MUST BE ACTIONABLE), observability is NOT a
footnote — each step MUST ship with the OBSERV surface below, and NO map/entity may be lost silently.

Phase-boundary COUNTS (so a drop between stages is visible, like the images 30→21→11 trace that
exposed the original bug):
- Step 1 (Phase 1): `OBSERV maps.parse book=.. maps_found=N refs=M` — count maps + total reference
  mentions captured; a map with 0 paragraph_numbers is a loud `status=warn` (provenance gap).
- Step 5 (Phase 3): `OBSERV maps.analyze in=N downloaded=D classified=C linked=L` — if
  downloaded<in or linked<in, emit `status=warn maps.analyze_shortfall` naming the gap (never a
  silent drop; mirrors equipment persist_shortfall).
- Step 6: after Phase-2 maps removal, assert Phase-2 emits ZERO map OBSERV (no dead path left).

LOSS/ERROR paths (every one loud + actionable, `status=error|warn`):
- download failure → `OBSERV maps.download_failed url=.. reason=..` (actionable: which map, why).
- classify/vision failure → `OBSERV maps.classify_failed` / the backstop's own error.
- find-or-create (Step 4): `OBSERV entity.backfill entity=place|org name=.. action=appended|created`
  so every fed-back entity is traceable; a create that can't enrich → `status=warn`.

IDEMPOTENCY accounting (Step 5/7 — closes the standing "don't resubmit maps to Grok" TODO):
- `OBSERV maps.vision calls=K skipped_already=S` per run; a re-run over an already-processed book
  MUST show `calls=0` (the test asserts it). A non-zero re-run count is an actionable regression.

ACTIONABLE discipline: a `status=error` must be a REAL anomaly (never cry error on an expected 0,
e.g. a chapter with genuinely no maps → `status=ok maps_found=0`, not error). The deterministic
map-count floor (how many `/maps/` URLs the parser saw) gates the yield verdict like other entities.

## Ordering rationale
1-2-3 are low-risk foundations (parser + de-dup + schema). 4 enables the feedback. 5 is the core
move (depends on 1-4). 6 removes the old path (depends on 5). 7 (infra feedback) ships last with the
most review. Each step leaves the pipeline working.

## Cross-cutting (every step)
- Provenance always (paragraph_numbers mandatory) · never-drop + actionable OBSERV (see Observability section — a per-step acceptance criterion, not optional) · gate (radon C,
  black on .py) · dual review (code + security) · CI green before merge · schema-version workflow on
  any schema change · grep `tests/` for stale fixtures on a version bump.
