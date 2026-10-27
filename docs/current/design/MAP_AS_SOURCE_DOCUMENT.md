# Map as a source document — spec

**Status:** DRAFT / proposed (owner-requested). No code written yet.
**Owner intent (verbatim):** "a map is the source document, from it an event is derived, and everything
flows out of the normal data pipelines." A vision-verified map should enter the pipeline like any other
source (a book chapter), produce an **event**, and let people_groups / places / dates / weather /
person-attachment fall out of the **existing `event_mentions` spine** — no special-case inference chain,
no new derived entity.

This spec is grounded in the current code (file:line). It defines the ONE adapter + the ONE prompt
change needed, the data contracts, provenance, validation, risks, and a phased build plan.

---

## 1. Why this fits the existing architecture (feasibility basis)

The pipeline already has the seam that makes this clean:

- **Event extraction consumes a GENERIC parsed structure, not "text."** `events.create_event_prompt`
  (src/extraction/events.py) reads `parsed_data["chapter_title"]`, `parsed_data["paragraphs"][].{text}`,
  and `parsed_data.get("images", [])[].{url, alt_text}`. It does not care whether those paragraphs came
  from prose or from a map description.
- **Everything downstream joins through `EventID`/`Sub_eventID`** via the shared `EVENT_MENTIONS_SCHEMA`
  (src/schemas/__init__.py:150) embedded in people, places, dates, groups, equipment, logistics, weather.
  Once a map yields an event, the normal Phase-2 entity extraction + Phase-3 enrichment produce
  people_groups/places/dates/weather that reference that event — with zero map-specific logic.
- **A non-book source already enters ingestion** via `SourceMetadata` (src/ingestion/source_metadata.py)
  — the same front door the URL-review affirm path and the `.html`→markdown converter feed. A map is the
  same idea: a non-prose source normalized into the parse contract.

Therefore the feature reduces to: **(A) an adapter that renders a vision-verified map into the parsed
contract, and (B) a richer `map_vision` prompt that extracts the depicted facts.** No new pipeline phase,
no new derived entity, no change to the event/entity/weather schemas' core.

---

## 2. The two pieces to build

### (A) Map → parsed-source adapter
Produce, for each affirmed map, a source + a parsed file in the SAME shape Phase 2 already scans
(`output/content/<source>/*-parsed.json`, see phase2_extract.py scan + `create_event_prompt`):

```
ParsedSource (map):
  book            = map source id (e.g. "map-<MapID>" or a human title)   # -> event_mentions[].book provenance
  chapter_number  = 1
  chapter_title   = map title / caption
  paragraphs      = [ {absolute_number: 1, text: <vision-derived description, see (B)>} , ... ]
  images          = [ {type:"external", url:<approved_image_url>, alt_text:<map title>} ]
```

- `paragraphs[].text` is the **vision-derived narrative** of the map (units, sector/front line, date
  window, operation, movement) — this is what the event extractor reads.
- Written through the SAME guarded path as other content (`write_json_with_lock`) so the schema/version
  stamp + validation apply uniformly.
- Source provenance: a `SourceMetadata` with `acquisition_method` (NEW value `"map_vision"` added to the
  `AcquisitionMethod` Literal, src/ingestion/source_metadata.py:29) + `acquisition_url` = the map's source
  page, `source_id` = the map id. This makes every entity derived from the map traceable to the map
  (A3/source-traceback already requires every entity to have a source).

### (B) Richer `map_vision` extraction (prompts/map_vision.yaml)
Today `map_vision` is a relevance verifier (is this a real WWII map of place X? yes/no — used by
`search_external_maps._verify_single_image`). Extend it (or add a sibling `map_extract` prompt) so, for an
APPROVED map, the vision model also returns a STRUCTURED description:

```
{ "is_relevant": bool, "is_government": bool,          # existing verify contract (unchanged)
  "depicted": {
    "units":        [ "116th Panzer Division", ... ],   # -> people_groups via normal extraction
    "places":       [ "Schmidt", "Kall trail", ... ],    # -> places
    "front_line_or_sector": "Hürtgen Forest, Roer sector",
    "date_or_window": "2-3 November 1944",               # -> dates
    "operation":    "First attack on Schmidt",
    "movements":    [ "US 28th Div attack SE toward Schmidt", ... ],
    "description":  "<prose paragraph(s) suitable as parsed text>"
  } }
```

The `depicted.description` (+ a rendered summary of the structured fields) becomes the adapter's
`paragraphs[].text`. The vision model is ALREADY looking at the image for the yes/no verify — we ask it for
more in the same call.

---

## 3. End-to-end flow (how it rides the normal pipeline)

```
OpenSERP/vision map path (Phase 3, existing)        NEW adapter                  NORMAL pipeline
-------------------------------------------         -----------                  ---------------
import_openserp_maps -> _verify_map_relevance  ->   on APPROVE:                  content-uploaded /
  _verify_single_image (map_vision)                 write SourceMetadata +       queued parse:
  [NOW also returns `depicted`]                     contentrepository/map-<id>/  Phase 1 (noop/normalize)
                                                     <id>-parsed.json             Phase 2 extract:
                                                     (paragraphs = vision desc,     events + Sub-events
                                                      images = approved image)      people_groups, places,
                                                                                    dates, equipment via
                                                                                    event_mentions spine
                                                                                  Phase 3 enrich:
                                                                                    geocode places,
                                                                                    NOAA weather by
                                                                                    PlaceID+date (nearest
                                                                                    station) — ALREADY keyed
```

The inference chain the owner described now **materializes as ordinary entities**, all sharing the map's
`EventID`:
- map → event (derived by extraction)
- event → people_group(s) (units depicted) + place(s) + date window  (normal Phase-2 entities, same EventID)
- person → unit: existing `people.units_served[].GroupID` / `group_affiliations[].GroupID`
  (src/schemas/people_output.py:92,109) — a person already in that unit is transitively placed
- place + date → weather: existing `weather.location.{PlaceID, lat, lon}` + `date` + NOAA nearest-station
  (src/enrichment/noaa_weather.py) — already keyed, no change.

No "unit-placement" derived entity is needed (the earlier alternative) — the event IS the placement fact,
with the map as its cited source.

---

## 4. Provenance & confidence (map-derived facts must be distinguishable)

- `event_mentions[].book` = the map source id (traceability).
- `SourceMetadata.acquisition_method = "map_vision"` marks the source type.
- The derived event carries `source_type="map"` (or equivalent marker on the event/source_section) so
  downstream consumers + dedup know these are MAP-derived (vision-inferred), not prose-stated. A map often
  IMPLIES a date/unit rather than stating it — the marker lets validation/UX treat confidence
  appropriately and lets a reviewer audit map-derived events separately.
- Optional: a confidence score from the vision step, persisted on the source/event.

---

## 5. Validation / integrity (reuses existing guards)

- Map-derived events are first-class events -> covered by the existing referential repair + validator
  (A3/B1 EventID/Sub_eventID audit; the repair/stale-tree discipline fixed in #571/#581).
- A3 source-traceback: every derived entity must have `event_mentions` with a resolvable source — the map
  SourceMetadata satisfies this like a book.
- The map's own `maps`/`external_maps` record is unchanged (still written); the NEW thing is the ADDITIONAL
  parsed-source + event derivation. A map thus produces BOTH: (1) its catalog `MapID` record, and (2) the
  event(s) + entities derived from reading it.
- Dedup: map-derived entities flow through the same Phase-2 name-index + post-Phase-2 dedup gate + the
  write-time result_dedup. The content-hash dedup (#578) prevents re-ingesting the same map image twice.

---

## 6. Risks / open questions (honest)

1. **Extraction quality from imagery is the real risk (not architecture).** Reading "116th Panzer at
   Schmidt, 2-3 Nov 1944" off a map image is harder + more variable than from prose (hand-drawn vs.
   printed, legible labels vs. not, legends, scale). Expect lower/variable yield than text sources. This is
   tunable (prompt, model, per-map confidence gating) and does NOT block the design.
2. **Hallucination guard.** The vision model must not invent units/dates not depicted. Mitigate: require
   `depicted` fields to be grounded ("only what is labeled/legibly shown"); low-confidence → store the map
   catalog record but SKIP event derivation (don't fabricate events).
3. **Confidence marker is mandatory**, not optional — map-derived events should be filterable so a bad map
   read can't silently corrupt the corpus.
4. **map → unit precision:** a map may show many units. Each depicted unit becomes a people_group via
   normal extraction (fine), but "which unit was WHERE" is resolved by the event/sub-event segmentation the
   extractor already does on the description — quality depends on (B)'s description richness.
5. **Which maps feed this?** Scope initially to the OpenSERP/vision-approved maps already in Phase 3; a
   future lane could ingest operator-uploaded map images via the same adapter.

---

## 7. Phased build plan

- **P0 — proof (narrow, 1 map):** add the `depicted` extraction to `map_vision` + the map→parsed adapter;
  manually drive ONE vision-approved map through Phase 2 and confirm it yields a valid event +
  place/people_group/date entities with the map as source. Validates the seam end-to-end.
- **P1 — wire into the map path:** on approve in `import_openserp_maps`/`_verify_single_image`, emit the
  SourceMetadata + parsed-source so the normal queued-parse/Phase-2 picks it up. Add `acquisition_method
  "map_vision"` + the `source_type="map"` marker + confidence.
- **P2 — quality + guardrails:** confidence gating (skip event derivation below threshold), hallucination
  grounding in the prompt, observability (OBSERV map_source.derived events=/entities=), and a validation
  slice for map-derived events.
- **P3 — reach:** operator-uploaded map images via the same adapter; optionally surface map-derived events
  distinctly in any review UI.

Each phase: gate + concurrent code+security review + PR, per the standing workflow.

---

## 8. Summary

Feasible and architecturally consistent. The map becomes a **source document**; an **event** is extracted
from its vision-derived description; **people_groups / places / dates / weather / person-attachment** all
flow out of the **existing `event_mentions` spine** with no new pipeline machinery. The work is an
**ingestion adapter (map → parsed source) + a richer `map_vision` extraction prompt + a `source_type=map`
provenance/confidence marker**. The only material risk is **extraction quality from map imagery**, which is
tunable, not fundamental.
