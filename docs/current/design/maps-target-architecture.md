# Maps Target Architecture (owner-specified, 2026-11-02)

**Status:** DESIGN (owner direction captured) · supersedes the "Recommendation" section of
`maps-architecture-review.md` · grounded in the real SNS→SQS→`trigger_handler` orchestration
(`cloudformation/events.yaml`, `lambda_handlers/trigger_handler.py`).

Owner decision: consolidate the accreted 8-module maps subsystem onto a clean per-phase split, with
cross-phase feedback via SQS. Phase 2 map processing is **eliminated**; map ANALYSIS lives in
Phase 3 for consistency; Phase 1 does only minimal extraction + reference capture.

---

## Per-phase responsibilities (target)

### Phase 0 — Ingestion (UNCHANGED)
- Extraction/ingestion stays here: source → markdown (pandoc), media detection, OOB tables, etc.
- Maps ride along as the raw-HTML `<a href><img>`/anchor references pandoc preserves. No change.

### Phase 1 — Parse (MINIMAL map handling)
Map processing here is limited to:
1. **Extract the map IMAGES** — capture each map's (hi-res) URL from the parsed document (the #672
   anchor logic: `<a href="maps/-N.jpg">`, link-only `<a href="maps/-XII.jpg">Map XII</a>`, hi-res
   preferred over `-Nm` thumbnail).
2. **Capture HOW each map is referenced** in the original document — e.g. "Map VIII is mentioned 3
   times, at these paragraph/sub-event locations" (reference list: map_id → [paragraph_numbers /
   sub-event anchors], with the verbatim mention text for provenance).
3. **Write a BARE-MINIMUM Map JSON** to go with it — MapID + source_url (hi-res) + figure/map_id +
   the reference list + source_book/provenance. NO classification, NO download, NO entity linking,
   NO analysis at this stage.
- Net effect: Phase 1 emits a stub Map record + its in-document reference footprint. Nothing more.

### Phase 2 — ELIMINATED for maps
- Remove `src/extraction/maps.py` from the Phase-2 flow entirely (the broken `[Map X](http://...)`
  text-regex finder + its download/classify/persist). Phase 2 no longer does ANY map work.
- (Phase 2 continues to own the OTHER entities.)

### Phase 3 — Map ANALYSIS (consolidated home)
All map analysis moves/stays here, for consistency with the other enrichment analysis:
- Download the map image (if not already), classify (`map_type`), and — the substantive work —
  link the map to **Events / Places / Dates / Organizations** (people_groups), run reverse map
  registration (`map_registration`), and the map-as-source adapter (`map_source_adapter`) where a
  map's own content is extracted.
- ORGANIZATION linkage (owner 2026-11-02) is a NEW dimension and requires grounded additions:
    * Map schema (`src/schemas/maps_output.py`) currently has `EventID` / `PlaceMentionID` /
      `DateMentionID` but NO organization field — ADD a `PeopleGroupID` (+ optional
      `PeopleGroupMentionID`) nullable ULID field (schema version bump).
    * `map_registration` currently keys the coverage extent on `covered_places (PlaceID) × date_
      range` only — EXTEND it to also carry the organizations operating in that extent so an org
      (people_group) whose activity falls inside a map's extent gets a back-link to the map, like
      places/dates do today.
- The tiered classifier + Grok-vision backstop ("is this an image of a map?") for AMBIGUOUS images
  lives here (Phase 3), batched + idempotent (see Feedback/Idempotency).
- The existing web-search map modules (`openserp_maps`, `grok_search_maps`) remain Phase-3 and are
  unchanged — they are the separate "no map in source → find one on the web" feature.

---

## Cross-phase feedback (SQS)

Existing mechanism (grounded): SNS topics → a single `TriggerQueue` (SQS) → `trigger_handler`
Lambda → launches the right phase task. Today:
- `ContentUploadedTopic` → Phase 1
- `ChapterParsedTopic` → Phase 2
- `EntityCreatedTopic` → Phase 3

Target feedback loops:

1. **New `people_groups` / `places` entities created in Phase 3 → FEED BACK to Phase 2.**
   When Phase-3 map analysis (or any Phase-3 step) mints a NEW people_group or place that Phase 2
   should incorporate (e.g. a place first identified while analysing a map), publish it so Phase 2
   re-processes to pick it up.
   - **NET-NEW:** today `EntityCreatedTopic` re-triggers **Phase 3**, not Phase 2. A **Phase-2
     re-entry path is not currently wired as a feedback target** — this is the "we may also need a
     Phase 2 SQS interface" the owner flagged. Build: a topic/route (e.g. a Phase-2 subscription or
     a `pending#phase2#<book>` re-entry) that `trigger_handler` maps to a scoped Phase-2 re-run.
   - Must be SCOPED (only the affected book/entities) and LOOP-SAFE (a Phase-2 re-run that mints no
     new feedback entities must terminate — no infinite Phase2↔Phase3 ping-pong).

2. **New Map entities → FED BACK for additional Phase-3 processing.**
   A newly-created Map entity re-enters Phase 3 for the downstream analysis (registration, linking,
   vision). This is ALREADY largely the `EntityCreatedTopic → Phase 3` path — confirm a Map entity
   publishes to it and `trigger_handler` routes it to a scoped Phase-3 re-run.

### Idempotency (ties to the open TODO)
- Phase-3 map vision (`grok_search_maps.verify_map_with_vision`, `cache_type=vision_verification`)
  and the new tiered backstop MUST NOT re-submit an already-classified map on every re-run. Persist
  the verdict + a `map_vision_checked_at` gate; the feedback loop re-processes only NEW/changed
  maps. Assert via OBSERV that a re-run over an already-processed book issues ZERO new vision calls.

---

## Migration / build order

1. **Phase 1:** emit the minimal Map stub + reference footprint (extend `parser.extract_maps` to the
   anchor form so `maps[]` is actually populated; write the bare JSON). Single source of truth =
   the parser's map list.
2. **Phase 2:** delete the maps path (`_extract_maps` call + retire/neuter `maps.py` map-finding).
3. **Phase 3:** move download/classify/link/registration here; add the tiered classifier + vision
   backstop (idempotent).
4. **SQS:** add the Phase-2 re-entry feedback interface (net-new); confirm Map→Phase-3 re-entry via
   `EntityCreatedTopic`; add loop-safety + scoping + idempotency OBSERVs.

## Open items to confirm with owner
- OI1: the exact trigger for Phase3→Phase2 feedback — any new place/people_group, or only ones a
  downstream entity references? (Scope control.)
- OI2: loop-termination policy for Phase2↔Phase3 (max rounds / converge-when-no-new-entities).
- OI3: does the minimal Phase-1 Map JSON need the reference footprint as paragraph_numbers,
  sub-event IDs, or both? (Provenance granularity.)
