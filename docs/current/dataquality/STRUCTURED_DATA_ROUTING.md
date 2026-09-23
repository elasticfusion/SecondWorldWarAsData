# Structured vs. Unstructured Data Routing

Design for classifying source documents at ingestion and routing structured
(tabular) data through the same entity model as unstructured (prose) data.

**Status:** Partially implemented (ingestion front-end + OOB markdown parsers built; docx/epub/txt + web-video/transcript converters built; entity convergence pending) | **Last Updated:** 2026-09-23

---

## Governing invariant: every fact traces to its source

The non-negotiable requirement the whole design serves: **every asserted fact
must be traceable back to the original source material, in a thoroughly
documented way.** Where in the pipeline this is enforced does not matter; the
guarantee does. A number in a table, a sentence of prose, a claim spoken in a
video, or an assertion on a web page must all resolve backward to their origin.

This invariant is already realized in pieces — `mentions[]`/`event_mentions[]`
(entity → sub-event → source), `verbatim_reference` and `provenance_anchor`
(page/offset), the `bibliography` record, the `acquisition` block (below), and
`biography_sources` on OOB-derived people. It is stated here once so no path is
built that breaks it.

### Required source/citation metadata (per document)

Every ingested source must capture the following, to the extent the medium
allows. Formally published works are expected to carry all of these;
self-published or archival material captures what exists and **flags the gaps
(`needs_review`)** rather than omitting the fields:

| Field | Where it lives today | Notes |
|-------|----------------------|-------|
| Author | `bibliography.citation.author[]` | |
| Copyright / status | `bibliography.copyright_status`, `license` | enumerate explicitly |
| Publish date | `bibliography.citation.publication_date` | |
| Publisher | `bibliography.citation.publisher` | |
| Publisher location | `bibliography.citation.publication_location` | |
| Website URL (if applicable) | `bibliography.resource_urls[]`, source-metadata `acquisition_url` | |
| — Capture date (for web) | *gap — add* | when the URL was fetched/snapshotted |

The web **capture date** is the notable gap: web sources are mutable, so the
fetch/snapshot timestamp is required to make a web-sourced fact reproducible.
It belongs on the source-metadata record (alongside `detected_at`/`checksum`)
and, for cited web pages, on the bibliography/acquisition record.

---

> **Reconciliation note (2026-09-18).** The ingestion front-end and the
> scanned-OOB structured extraction described here have now been **built** in
> `src/ingestion/` (see [INGESTION_FRONT_END.md](INGESTION_FRONT_END.md)). The
> implementation differs from parts of the original proposal below in ways that
> are called out inline — most importantly: (a) disposition is classified by a
> **per-page heuristic classifier**, not only by declarative per-source meta;
> (b) scanned tables are parsed from the **Chandra OCR+AI markdown** by
> purpose-built section parsers, while the generic **CSV field-map** remains the
> path for cleanly-CSV sources; (c) parsed rows are persisted to `output/oob/`
> and linked to `PersonID` via a **non-destructive crosswalk** (reversible,
> enabling later fuzzy matching), with full **entity convergence at dedup** as
> the remaining bridge step. Inline "**Built:**" / "**Pending:**" markers below
> record current status.

---

## Problem

The pipeline was built for prose. Everything flows through one shape:

```
markdown prose → discovery → parse_chapter → paragraphs[] → Phase 2 (Grok) → entities (ULIDs) → dedup → Phase 3
```

That single assumption — "a source is prose markdown already sitting in
`contentrepository/`" — breaks against the real range of source material the
project now handles or intends to. Five distinct problems:

1. **Non-markdown media formats are invisible.** `discovery.py` requires
   `chapterN/chapterN-content.md` + `-meta.yaml`. Downloaded PDFs (Eisenhower
   Subject Guides ~31, Donovan Research Library ~122) sit in
   `contentrepository/` as raw `.pdf` and never enter Phase 1. The same gap
   applies to HTML (handled today only as a one-off for ibiblio/HyperWar),
   EPUB, and plain text.

2. **Tabular data terminates in silos.** The ETO Order of Battle is extracted
   by a 4,374-line bespoke script into 8 flat CSVs that **never enter the
   entity store**. Officers, divisions, and dates from the OOB tables are never
   ULID'd and never deduped against prose entities. "Maj Gen Huebner" from the
   OOB table and "Huebner" from the Ardennes prose remain unlinked records. Each
   new structured source risks becoming another multi-thousand-line silo.

3. **Still and moving images have no home — and maps are mis-recognized.**
   Map scans, official/unofficial event photography, film footage, and headshots
   are either downloaded as dead files or handled by narrow, source-specific
   code. There is no consistent path that registers them as cross-referenced
   media entities (so a Huebner headshot can link to the Huebner Person entity
   regardless of where he was first found).

   Embedded images fare a little better than maps but share the same disease.
   An `<img>` in ibiblio HTML *is* captured — HyperWar converts it to
   `![alt](url)`, `parser.extract_images` catches it, and `images.py` (Phase 2)
   creates an `ImageID` entity linked to a sub-event/place/date. But:
   - **Classification is keyword-only and inconsistent.** `images.py`
     `_classify_content_type` substring-matches alt/URL (`"map "`, `sketch`,
     `chart`) — a *different* detector from `parser.extract_maps`' `[Map N]`
     link pattern. A map captioned "Situation 17 December" or "Plate III" is
     misclassified as a photograph.
   - **`images.py` skips anything it thinks is a map**, deferring to the maps
     extractor — but that extractor only sees `[Map N]` *links*, never `<img>`.
     So an embedded image-map falls in the gap between the two and is **lost
     entirely**.
   - **Dedup is by `alt_text`** — distinct images sharing blank/duplicate alt
     text (common in scanned HTML) are silently dropped.
   - **No vision** ever inspects the pixels; a headshot becomes a generic
     "photograph" titled "Untitled" and is not identified as a portrait of a
     specific person.

   Recognition quality is hostage to the source author's alt-text discipline,
   via two inconsistent keyword classifiers, with no content-based check.

4. **Audio/video sources are unmodeled.** Eisenhower oral histories and Library
   of Congress veteran narratives are audio/video. Their *content* is prose once
   transcribed, but nothing converts them or links the resulting entities back
   to the recording as a media asset.

   > **Update (2026-09-23) — addressed.** `src/ingestion/web_video.py` now models
   > the web-page-with-video case: the page is captured as a source record with
   > `(url, capture_date)` and the embedded video is registered as a media asset
   > whose transcript is split into **timecoded segments**, so a spoken assertion
   > binds to `(asset_id, start–end)` — the audio/video analogue of
   > `verbatim_reference` + page number. `GrokTranscriber` provides a real
   > backend (xAI `/v1/stt`, word-level timestamps); `NullTranscriber` (default)
   > flags the asset `needs_review` rather than fabricating a transcript.
   > Acquisition wiring for the specific oral-history collections remains to do.

5. **The footnoted-source acquisition problem is entirely absent.** The ultimate
   goal is extracting the data *behind the footnotes*, not just the narrative.
   Of ~13,500 identified citations, **72% (`archive`/`offline`) are physical
   holdings** (NARA, Bundesarchiv) that must be systematically acquired —
   requested, digitized, received — before their content can be ingested. Only
   ~3,600 are directly available online. The pipeline identifies these sources
   (`archive_reference_number`, `archive_physical_address`) but has no lifecycle
   to acquire them and feed them back in.

Underlying all five: the pipeline makes an ingestion decision *implicitly and
too late* (it assumes prose markdown). It needs to classify each source's
medium and content type **at the outset** and route accordingly, while
converging every path on the same ULID'd entity model.

---

## Design

### 1. Classify at discovery (declarative)

Each source declares its type in its meta file. The person adding a source
knows what it is — don't rely on fragile content sniffing.

```yaml
# contentrepository/{Source}/{item}/item-meta.yaml
content_type: prose        # prose | tabular | media | mixed
source_format: markdown    # markdown | html | pdf | csv | epub | docx | txt
                           #   | image | map | video | audio
media_type: null           # (when content_type: media) photograph | map
                           #   | film | audio | drawing
```

Defaults (`content_type: prose`, `source_format: markdown`) preserve all
current behavior. `discovery.py` reads these and tags each `ChapterGroup` (or,
for media, a new `MediaGroup`). Two axes:

- **`content_type`** — what the pipeline does with the extracted meaning:
  `prose` (Grok extraction), `tabular` (field-mapped), `media` (asset + vision),
  `mixed` (a PDF with both narrative and OOB tables).
- **`source_format`** — the physical medium Phase 0 must normalize first.

> **Built vs. designed — classification.** The declarative meta is still the
> intended way for a contributor to *assert* a source's type. In addition, a
> **per-page heuristic classifier** was built
> (`src/ingestion/disposition_classifier.py`) that determines each page's
> disposition (`structured` | `unstructured` | `image` | `map`) directly from
> the content — essential because a single scanned PDF is *mixed* at the page
> level and cannot be described by one document-level `content_type`. It is
> **scanned-aware**: on a fully-scanned PDF (where page geometry cannot reveal
> structure) it defers structure recovery to the OCR+AI markdown rather than
> guessing. So classification is now two-layer: declarative meta per source,
> heuristic disposition per page/region. See INGESTION_FRONT_END.md.

### 2. Phase 0 — format-agnostic ingestion normalization

Phase 0 is not "PDF→markdown." It is a **dispatcher** that normalizes any
source medium into one of two destinations:

- **Text destination** — parseable content for the entity pipeline
  (`chapterN-content.md` or a CSV for the tabular parser).
- **Media destination** — a registered asset in `filestore/` with a
  corresponding `images`/`maps`/`media` entity (ULID, caption, license,
  source, and cross-references), reusing the existing vision-verification path.

The medium is declared per source (`source_format`) and Phase 0 dispatches to
the right handler. Most handlers already exist in some form — Phase 0 is the
front door that unifies them.

| source_format | Handler | Destination | Status |
|---------------|---------|-------------|--------|
| `markdown` | none (ready) | text | ✅ exists |
| `html` | HTML→markdown (HyperWar converter, generalized) | text | ⚠️ exists for ibiblio only |
| `pdf` | Chandra OCR → markdown | text | ✅ **Built** (`src/ingestion/region_converter.py`; media detection + per-page disposition classify → convert) |
| `pdf` (scanned tables) | Chandra OCR markdown → **section parsers** → rows | text | ✅ **Built** (`src/ingestion/oob_markdown/*`: command-staff, campaigns, command-posts, statistics, organic-units + division inference) — see note below on markdown-parser vs. CSV-field-map |
| `csv` | tabular parser (declarative field-map) | text (entities) | ❌ to build (still the right path for *cleanly*-CSV sources) |
| `epub` | epub→markdown (pandoc) | text | ✅ **Built** (`src/ingestion/text_converters.py`, `epub_to_markdown`) |
| `docx` (Word) | docx→markdown (pandoc) — carries embedded images/tables | text (+ media) | ✅ **Built** (`src/ingestion/text_converters.py`, `docx_to_markdown`) |
| `txt` | wrap as markdown | text | ✅ **Built** (`src/ingestion/text_converters.py`, `txt_to_markdown`) |
| `image` (still) | register asset + vision caption/OCR | media entity | ⚠️ `images.py` + region converter extract embedded images |
| `map` (still) | register asset + vision verify | `maps` entity | ✅ exists |
| `video` (moving, incl. web-page-with-video) | register asset + **timecoded transcript** + linked page-capture | media entity | ✅ **Built** (`src/ingestion/web_video.py`; `GrokTranscriber` → xAI `/v1/stt` word-level timecodes, `NullTranscriber` default flags `needs_review`) |
| `audio` (oral history) | transcribe → markdown + register asset | both | ⚠️ transcription path exists via `GrokTranscriber`; oral-history acquisition wiring pending |

> **Built vs. designed — scanned tables.** The original design (§4 below) routed
> tabular data through a generic **CSV → entity field-map**. What was actually
> built for the ETO Order of Battle instead parses structure directly from the
> **Chandra OCR+AI markdown** with purpose-built section parsers, because that
> markdown is where the scanned document's table structure and cleanest cell
> text actually live (validated: cleaner than the legacy CSVs — see
> INGESTION_FRONT_END.md). The generic CSV field-map (§4) remains the intended
> path for sources that arrive as *clean* CSV/HTML tables. The two coexist:
> markdown-parser for scanned OCR tables, field-map for clean tabular sources.

This makes the downloaders' PDF dumps first-class inputs, brings HTML handling
out of the ibiblio-only special case, and gives still/moving images and audio
(oral histories, event footage, headshots, map scans) a defined home instead
of being ad-hoc.

#### Media as entities, not dead files

A downloaded headshot, map scan, or event photo should become an entity:

```json
{
  "ImageID": "01K...",
  "asset_path": "filestore/images/01K....jpg",
  "media_type": "photograph",        // photograph | map | film | audio | drawing
  "caption": "Maj Gen Clarence R. Huebner, 1st Infantry Division",
  "source": "Eisenhower Presidential Library",
  "license": "Public Domain",
  "vision_verified": true,
  "people": ["01K...huebner"],        // cross-refs, so dedup + Phase 3 can link
  "places": [], "events": [], "dates": []
}
```

Because it carries cross-references, a headshot found for Huebner links to the
same Person entity whether he came from OOB tables or Ardennes prose — and the
Wikipedia/OpenSERP portrait enrichment (Phase 3) can dedup against it instead
of re-fetching.

#### Classify media by content, not by the author's wording

The `media_type` above must be decided by **vision**, not by regex on caption
or link text. Today `parser.py` only tags a `Map` when it sees the literal
`[Map N](url)` link pattern, so embedded HTML/PDF map images (converted to
generic `![alt](url)`) are silently treated as ordinary images and never reach
the existing `_classify_map_type` + vision path.

Under Phase 0, **any** image — embedded `<img>`, linked, or extracted from a
PDF page — is registered as a media asset first, then vision decides its
`media_type` (map / photograph / chart / drawing / insignia). The existing
maps vision-verification becomes one branch of a general media classifier
rather than a gate that most images never reach. Caption/alt text and any
"Map"/"Plate"/"Sketch" wording become *hints* to the classifier, not the sole
trigger. This fixes the embedded-map miss (including in ibiblio) and gives
photographs, unit insignia, and charts the same first-class treatment.

#### Audio/video (oral histories, event footage) and web pages with video

The Eisenhower oral histories and LoC VHP narratives are the near-term driver:
- **Audio** → transcribe (whisper) → the transcript is prose that flows through
  the normal entity pipeline; the audio file is registered as a linked media
  asset.
- **Video** → transcript + sampled keyframes (keyframes can themselves go
  through vision captioning); the film is a media entity linked to the events
  it depicts.

**Web page containing video** is a distinct, first-class case (requirement:
track any web-asserted fact back to source). Such a page yields *two* linked
provenance objects, both retained:

1. **The web page itself** — captured as a media/source record with its URL and
   **capture date** (see required-metadata table above), plus the page text.
   The page text is *later summarized*; the summary is an entity that cites the
   captured page, so the summary never floats free of its source.
2. **The embedded video** — registered as a media asset with a **transcript**.
   Each transcript segment carries its **timecode**, so a specific spoken
   assertion (a general's or politician's statement) binds to
   `(video asset, start–end timestamp)`. The transcript is prose that flows
   through the entity pipeline; extracted claims/quotes reference the timecoded
   segment, not just the video as a whole.

Binding: the web-page record and the video asset are cross-referenced to each
other and to the same events/people, so a fact surfaced from the video resolves
to *both* the page it appeared on and the exact moment in the recording. This is
the moving-image analogue of `verbatim_reference` + page number for print — the
provenance anchor for time-based media is `(asset_id, timecode)` and, for the
containing page, `(url, capture_date)`.

These are later steps, but the classifier and media-entity model (and the
timecode/capture-date anchors) should be designed now so they slot in without
rework.

### 3. Parse paths converge on one entity model

```
discovery (reads content_type + source_format)
   │
   ▼
Phase 0 — normalize by source_format
   ├── markdown/html/pdf/epub/txt/audio → text  ──┐
   ├── csv / scanned-tables            → tabular ─┤
   └── image/map/video                 → media  ──┤
   │                                              │
   ▼                                              ▼
Phase 1/2                                   (all paths emit ULID'd entities)
   ├── prose   → parse_chapter → Grok  ──────────►  output/{people,people_groups,
   ├── tabular → tabular_parser + map ──────────►    dates,events,places,
   └── media   → asset register + vision ───────►    images,maps,...}/
                                                       │
                                                       ▼
                                                 dedup (merges across sources
                                                        and across media/prose)
                                                       │
                                                       ▼
                                                 Phase 3 enrich
```

Every path emits the **same entity JSON** with ULIDs and cross-references.
They converge at dedup, where existing name/position/shared-URL scoring merges
duplicates regardless of whether an entity came from prose, an OOB table, or a
captioned photograph.

> **Built vs. designed — convergence (the key reconciliation).** The end goal
> above — "Maj Gen Huebner from the OOB table and Huebner from the prose
> converge as one ULID'd Person at dedup" — remains the target. What was built
> so far deliberately stops one step short of it, for safety:
>
> - Parsed OOB rows are persisted to their **own store** (`output/oob/<section>/`)
>   via `src/ingestion/oob_markdown/persist.py`, and linked to existing people
>   with a **non-destructive name→`PersonID` crosswalk**
>   (`crosswalk.py`) — currently **exact normalized-name match only**.
> - This is intentional (design "C1"): it does **not** write into
>   `output/people/` at ingestion, because write-time dedup there is pure
>   normalized-name match with **no fuzzy step**, so merging garbled OCR names
>   (e.g. `McLuliffe`) directly would create false-new or, worse, false-merged
>   people in the authoritative store. Keeping OOB rows separate + a re-runnable
>   crosswalk preserves both inputs pristine so a **fuzzy / LLM-verified matcher**
>   can be applied later without redoing ingestion.
>
> **The remaining bridge to full convergence** is therefore: (1) a fuzzy/verified
> matcher that upgrades the crosswalk's `match_method` from `exact`/`none`, then
> (2) an emit step that turns confidently-matched OOB rows into the *same*
> `people`/`people_groups`/`dates` entity JSON (or merges into existing ones) so
> they flow through dedup exactly as the diagram above intends. Until then, the
> crosswalk *is* the linkage: reversible now, convergent later. The generic
> field-map (§4) describes the eventual emit shape for clean CSV sources; the
> OOB markdown parsers produce equivalent rows that the same emit step will
> consume.

### 4. Generic tabular → entity mapper

The key piece: a **declarative field-map** so we never write another bespoke
extractor. One CSV row → one or more entities + relationships.

#### Field-map config

```yaml
# structured_maps/eto_oob_command_and_staff.yaml
# Maps eto_oob_command_and_staff.csv → Person + Group + Date entities
source_csv: eto_oob_command_and_staff.csv
book: "European Theater of Operations - Order of Battle"

# The division column identifies a Group (military unit) entity
group:
  entity_type: people_groups
  name_field: division
  group_type: "Military Unit — Division"

# Each row produces a Person with a role linking to the group
person:
  entity_type: people
  name_field: name
  fields:
    rank: rank
    position: position          # e.g., "Comdg Gen"
  relationship:
    to: group                   # link this person to the division
    role_field: position
    date_field: effective_date  # when they held the role
    acting_field: acting        # boolean "acting" qualifier

# Dates become Date entities, cross-referenced
date:
  entity_type: dates
  value_field: effective_date
  precision: day
```

#### Mapper (single generic module — replaces all bespoke silos)

```python
# src/parser/tabular.py
def parse_tabular(csv_path: Path, field_map: dict) -> list[dict]:
    """Convert CSV rows to pipeline entities per a declarative field-map.

    Returns a list of entity dicts (people, people_groups, dates) with ULIDs
    and cross-references, ready for the entity store + dedup.
    """
    entities = []
    group_cache = {}  # division name → GroupID (dedup within this file)

    for row in _read_csv(csv_path):
        # 1. Group (division) — created once, reused
        group = _get_or_create_group(row, field_map["group"], group_cache)

        # 2. Person with role/assignment linking to the group
        person = _build_person(row, field_map["person"], group["GroupID"])

        # 3. Date entity cross-referenced by the person's role
        date = _build_date(row, field_map["date"])
        person["biographical_profile"]["positions"][-1]["dates"] = [date["DateID"]]

        entities.extend([person, date])
        if group["GroupID"] not in {e.get("GroupID") for e in entities}:
            entities.append(group)

    return entities
```

Each new tabular source needs **only a YAML field-map**, not code. The OOB's
8 CSVs become 8 small field-maps.

---

## Source acquisition lifecycle (the footnote-extraction goal)

The ultimate goal — extracting the *footnoted* data, not just the narrative —
is dominated by physical archives. Of ~13,500 bibliography entries today:

| availability | count | use case |
|--------------|------:|----------|
| `archive` | 9,235 | **Case 2 — physical acquisition** |
| `offline` | 535 | Case 2 |
| `online` | 3,635 | Case 1 — direct ingest |
| `unknown` | 158 | triage |

**72% of cited sources are physical.** `resolved` is only 467 — nearly
everything is *identified but not yet obtained*. Footnote extraction is
therefore primarily an acquisition problem, and acquisition is a long-running,
human-in-the-loop, sometimes-paid workflow the pipeline does not yet model.

### Two entry points, one state machine

Both use cases converge on the same normalization layer (Phase 0). They differ
only in how the bytes are obtained:

```
Bibliography entry (identified citation, has archive_reference_number
                    + archive_physical_address, availability flag)
        │
        ├── availability: online  ── CASE 1 ──► auto-fetch URL ───────────┐
        │                                        (partial today via        │
        │                                         resource_urls)           │
        │                                                                  ▼
        └── availability: archive/offline ── CASE 2 ──► ACQUISITION QUEUE  Phase 0
                                                         (state machine)   (format-
                                                              │            agnostic
                                                              ▼            handler)
                                                        obtained bytes ────┘
                                                        (PDF/image/scan)
```

### Acquisition state machine (Case 2)

A citation resolved to a physical holding needs lifecycle tracking the current
`search_status` (found/not_found) cannot express:

```
identified    ← Grok RG ID + NARA/archive catalog match (have today)
   ↓
requestable   ← catalog confirms it's orderable; capture order info
                (NARA order form, Bundesarchiv reproduction request, fee)
   ↓
requested     ← reproduction/pull request submitted (date, request ID, cost)
   ↓
awaiting      ← in the archive's queue (weeks–months)
   ↓
received      ← digital scan / copy in hand → hand to Phase 0
   ↓
ingested      ← normalized + entities extracted → close the loop
   ↓
(or) unavailable ← lost, restricted, missing-from-scan (cf. OOB coverage gaps)
```

New fields on the bibliography entry (additive):

```json
{
  "acquisition": {
    "state": "requestable",
    "repository": "NARA College Park",
    "record_group": "RG 407, Entry 427",
    "catalog_url": "https://catalog.archives.gov/id/...",
    "order_method": "reproduction_request",   // self_scan | reproduction | onsite
    "estimated_cost": "USD 0.80/page",
    "request_id": null,
    "requested_date": null,
    "received_date": null,
    "history": [ {"state": "identified", "at": "2026-..."} ]
  }
}
```

### What each case needs

**Case 1 (online, 3,635):** mostly works. Resolver finds `resource_urls`; a
fetch step should download the bytes and hand them to Phase 0. Gap today: the
fetch + handoff isn't automated end-to-end (URLs are stored, not retrieved).

**Case 2 (physical, 9,770):** needs the state machine above plus:
- **Batch request generation** — group citations by repository + record group so
  one NARA visit / reproduction order covers many footnotes at once (economical,
  and matches how archives actually operate).
- **Cost + priority triage** — 9,770 items can't all be ordered. Rank by how many
  events/people/sub-events cite each source; the `mentions` array already gives
  citation frequency, so high-mention sources are acquired first.
- **Human-in-the-loop gates** — a person submits the order and later uploads the
  received scan. The pipeline tracks state and resumes automatically on receipt.
- **Deduplicated ordering** — the duplicate-citation problem (already a TODO)
  matters more here: don't order the same NARA box twice.

### Why model it now

The classifier and Phase 0 handlers are being designed regardless. If the
bibliography entry gains an `acquisition` block and Phase 0 accepts "received
bytes for citation X," then:
- Case 1 is a short automated path (fetch → Phase 0).
- Case 2 is the same path with a human-gated queue in front.
- A received NARA scan flows through the *same* PDF/image handlers as any other
  source, and its extracted entities dedup against the prose that cited it —
  closing the footnote loop.

This does not require building the acquisition system now. It requires reserving
the `acquisition` state field and making Phase 0 citation-aware so both entry
points land in the same place.

---

## Migration path (incremental, non-breaking)

Foundation first, then one medium at a time. Each medium reuses an existing
handler where one exists.

> **Progress (2026-09-18).** The ingestion front-end and OOB scanned-table
> extraction are built in `src/ingestion/` (steps marked ✅ below). Remaining
> work is the entity-convergence bridge, pipeline wiring (Phase 0 as a runnable
> step), the clean-CSV field-map, and the media/audio/acquisition handlers.

**Foundation**
1. ✅ **Source metadata + media-type detection** — `source_metadata.py`,
   `media_detection.py` (media type + supported/unsupported flag). Declarative
   `content_type`/`source_format` on discovery still to be added. *(mostly done)*
2. ✅ **Phase 0 dispatcher skeleton** — per-page disposition classifier +
   routing manifest + region converter (`disposition_classifier.py`,
   `routing_manifest.py`, `region_converter.py`), no-op-safe for markdown.
   *(built as library; not yet wired as a runnable phase — see "Pipeline wiring")*

**Text-producing media**
3. ⚠️ **HTML handler** — generalize `import_hyperwar_html.py`. *(exists for ibiblio)*
4. ✅ **PDF handler** — region converter drives Chandra OCR → markdown, with
   image/map asset extraction. *(built)*
5. ❌ **txt / epub handlers** — trivial wrap / pandoc.

**Tabular (scanned OOB — built via markdown parsers)**
6. ✅ **OOB markdown section parsers** — command-staff, campaigns, command-posts,
   statistics, organic-units on a shared `_common` framework, with **division
   inference** (Signal 1) and verification-flagging. *(built)*
7. ✅ **Persist + crosswalk** — rows → `output/oob/<section>/`; non-destructive
   name→`PersonID` crosswalk. *(built — design "C1")*
8. ❌ **Generic `src/parser/tabular.py` + field-maps** — for *clean* CSV sources
   (and the eventual entity-emit shape). Still to build.

**Entity convergence + wiring**
9. ❌ **Fuzzy / LLM-verified matcher** — upgrade the crosswalk beyond exact match.
10. ❌ **Entity-convergence emit** — turn matched OOB rows into the same
    `people`/`people_groups`/`dates` entities so they flow through dedup (the
    Huebner goal).
11. ❌ **Pipeline wiring ("B")** — run the ingestion front-end as a Phase 0 step
    in both local and AWS/ECS execution paths.

**Media entities**
12. ⚠️ **Still image / map handler** — asset register + vision caption + cross-refs.
13. ❌ **Audio handler (oral histories)** — whisper transcript → prose path. *(future)*
14. ❌ **Video handler** — transcript + keyframes. *(future)*
14a. ❌ **Web-page-with-video handler** — capture page (URL + capture date, text→summary later) + register embedded video with timecoded transcript; cross-link both. *(future)*

**Acquisition loop (footnote extraction)**
15. ❌ **`acquisition` block on bibliography entries** — additive field + state enum.
16. ❌ **Case 1 auto-fetch** — download `resource_urls` bytes → Phase 0.
17. ❌ **Case 2 request batching + triage** — group by repository, rank by `mentions`.
18. ❌ **Receipt handoff** — received scan matched to citation → Phase 0 → dedup.

Steps 1–2 are the reusable spine (built). Steps 9–11 are the current critical
path to making the built work converge and run end-to-end.

---

## Why keep the OOB extractor's CSVs

The 4,374-line extractor solves a genuinely hard problem: OCR'd multi-page
military tables with continuation rows, insignia page detection, and scan gaps
(documented in `eto_oob_division_coverage_report.md`). That parsing logic is
worth keeping. The fix isn't to rewrite it — it's to add the CSV→entity mapper
downstream so its output stops being a silo. Future tabular sources that are
*cleanly* tabular (already CSV/HTML tables from Chandra) skip the bespoke step
and go straight through the generic mapper.

---

## Related

- [INGESTION_FRONT_END.md](INGESTION_FRONT_END.md) — the built ingestion
  front-end + OOB markdown parsers (media detection, per-page disposition,
  routing manifest, region converter, section parsers, division inference,
  persist + crosswalk)
- [CHANDRA_OCR_DESIGN.md](CHANDRA_OCR_DESIGN.md) — PDF→markdown OCR (Phase 0 bridge)
- [eto_oob_division_coverage_report.md](eto_oob_division_coverage_report.md) — OOB extraction coverage
- [../core/PIPELINE.md](../core/PIPELINE.md) — prose pipeline phases (now incl. Phase 0)
- [../SCHEMA_REFERENCE.md](../SCHEMA_REFERENCE.md) — entity schemas the mapper targets
