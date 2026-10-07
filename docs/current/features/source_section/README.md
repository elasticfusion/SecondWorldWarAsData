# Source Section

**Entity:** `source_section` · **Output:** `output/source_section/*.json` · **Schema:**
`src/schemas/source_section_output.py` · **Phase:** 2 (producer + enrichment)

## What it is

`source_section` is the **coarse-grained, source-neutral anchor** that sits ABOVE the granular
Event/Sub-event layer — **one record per top-level source section** (a book *chapter*, a
journal *article*, an after-action *report*, a German KTB *entry*). The name is deliberately
source-neutral: "Chapter" is a book-centric term for this general concept, and the pipeline
ingests many non-book sources.

There is exactly **one Event per source section** (the events extractor merges chunk-split
chapters back into a single Event), so `source_section` is a 1:1 anchor linked by `EventID`.
It exists so that section-level understanding — a short summary, the operation/campaign the
section is about, and reference material about that operation — has a natural home that would
be wrong to attach to any single granular sub-event.

## Fields

| Field | Meaning |
|---|---|
| `SourceSectionID` | ULID primary key |
| `section_title` | the source-section title (from the event's `Chapter`) |
| `section_summary` | a coarse summary of the whole section, **< 2 sentences** |
| `source` | provenance `{book, author, series}` |
| `EventID` | the per-section Event this anchors |
| `operation` | derived canonical operation/campaign `{name, wikipedia_title, aliases, confidence, source}` — **null when none applies** |
| `reference_articles[]` | Grokipedia + Wikipedia article text (additive) + their raw references |
| `wikipedia_checked_at` | gate marker (stamped even on a miss) |

## How it is produced (all Phase 2, native commit)

1. **Producer** (`source_section.emit_source_section`, hooked into `events._save_event_output`):
   emits one bare record per section as events are finalized (title + EventID; summary and
   enrichment null).
2. **Derivation pass** (`enrich_all_source_sections`, wired into `phase2_extract.py` step 4b):
   one LLM call over the **cumulative sub-event summaries + aggregated places/dates** (NOT the
   raw chapter — cheaper, no token-limit risk) derives:
   - `section_summary` (< 2 sentences), and
   - `operation` — the canonical name a Wikipedia reader would search. The chapter title is
     usually a book heading, not the operation name, so the operation is inferred from
     content. **Null-over-fake:** below a confidence threshold (default 0.6), `operation` is
     null rather than a forced guess.
3. **Article fetch** (`source_section_articles`): when an operation is present, fetch
   **Grokipedia** (text, best-effort — graceful URL-only if its JS body yields nothing) and
   **Wikipedia** (bounded text + full reference list) as additive `reference_articles`.
   References are captured **inline, raw, provenance-tagged** — promotion into the
   `bibliography` entity is a **backlog** item.
4. **Media** (`source_section_media`): pull **Wikipedia** photos + maps, write them as
   **first-class `images` records** cross-linked by `SourceSectionID` + `EventID`, with full
   provenance (source_url, Commons license, attribution; null-over-fake). Maps are tagged
   `image_type: "map"`. The GeoJSON `map_features` entity is produced only by the Grok-vision
   map-interior extractor, not fabricated here — a captured map raster can later feed it.

## Worked example

Ardennes book chapter **"THE ATTACK BY THE GERMAN LEFT WING: 16-20 DECEMBER"** →
`operation: "Battle of the Bulge"` (confidence 0.85), Wikipedia article (138 references) +
Grokipedia text, and real NARA combat photos + the "Wacht am Rhein" campaign maps written as
images cross-linked back to the section. A pure-logistics section → `operation: null`, no
fetch.
