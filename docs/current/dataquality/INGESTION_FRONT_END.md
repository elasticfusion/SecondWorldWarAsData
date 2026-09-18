# Ingestion Front-End: Media Detection & Disposition Routing

Status: design + phased implementation
Last updated: 2026-06-19

## Purpose

Add a **pre-processor** ahead of the existing pipeline that:

1. Records the original document as the source of truth.
2. Detects the media type (and flags unsupported media without failing).
3. Classifies each **page/section** by *disposition* — `structured`,
   `unstructured`, `image`, or `map` — because a single source document is
   frequently **mixed** (e.g. the ETO Order of Battle PDF has prose, rosters,
   insignia images, and maps in one file).
4. Emits a **routing manifest** describing which page/section maps to which
   disposition.

The manifest is then consumed by the existing conversion stage (stage 3), which
converts each region to Markdown using a disposition-appropriate method. The
Markdown remains the **universal intermediate**; downstream stage 4 breaks the
Markdown into JSON records.

This document describes the whole design. Implementation is phased; see
"Implementation status" at the end.

## Background: the pipeline today

```
Original (HTML | PDF)                         source of truth
      │
  STAGE 3  scripts/pdf_to_markdown.py         PDF  → Markdown (pymupdf4llm, WHOLE doc)
           scripts/import_hyperwar_html.py    HTML → Markdown (html2text, carries <img>)
      │
  STAGE 4  phase1_parse.py + src/parser.py    Markdown → parsed JSON
           (MarkdownDocument: paragraphs, images, maps, footnotes)
      │
  phase2_extract.py / phase3_enrich_data.py   entity extraction / enrichment
```

### The confirmed defect

`scripts/pdf_to_markdown.py` calls `pymupdf4llm.to_markdown(pdf_path)` on the
**entire PDF at once** and has **no concept of page range, section, or
disposition**. `pymupdf4llm` is prose/table oriented; it does **not** extract
embedded images or maps as retrievable assets.

Consequences for a mixed PDF:

- Image and map pages are flattened into (or dropped from) a prose Markdown blob.
- `phase1_parse.py` / `src/models.py` **already** have `images: List[Image]` and
  `maps: List[Map]` slots — but for PDFs there is nothing upstream to populate
  them from. (The HTML path works because HTML already carries `<img>` tags.)

So image/map extraction from PDFs fails **structurally**: there is no image/map
conversion path, and no mechanism to route specific pages to one.

## Architecture

```
Original document (recorded as source of truth)
      │
┌─────────────────────────────────────────────────────────────┐
│  INGESTION FRONT-END (new pre-processor)                      │
│                                                               │
│  1. Source-metadata record   (recorded original)             │
│  2. Media-type detection      doc-level; unsupported→flag     │
│  3. Disposition classifier    PER PAGE/SECTION, heuristic     │
│                               + config override + confidence  │
│  4. Routing manifest          page/section → disposition      │
└─────────────────────────────────────────────────────────────┘
      │  (hand-off: manifest)
  STAGE 3  region-aware conversion → Markdown  (per disposition)
      │
  STAGE 4  Markdown → JSON (unchanged)
```

The front-end **does not** parse content, extract entities, or convert to
Markdown. It answers two questions per source — *what media is this?* and *for
each page/section, what disposition?* — and records both. Existing stage 3/4
logic is not replaced or wrapped; a minimal seam lets stage 3 accept
`(page range, disposition)` instead of only a whole document.

### 1. Source-metadata record

The recorded original. One record per ingested source.

Fields:

- `source_id` — stable identifier (ULID, consistent with the entity store).
- `original_path` — retained path to the original document.
- `media_type` — detected type (see §2).
- `supported` — bool; `False` for recognized-but-unsupported media.
- `acquisition_method` — how it was obtained (`download`, `local`, …).
- `acquisition_url` — source URL/DOI when applicable.
- `checksum` — content hash of the original (change detection / versioning).
- `detected_at` — timestamp.
- `notes` — free text (e.g. why unsupported).

Rationale: "always record the original so problems can be fixed later." All
later stages (detection, classification, conversion, citation) reference this.

### 2. Media-type detection (document level)

Classes: `pdf`, `html`, `image`, `moving_image`, `unsupported`.

Detection order (cheapest, most reliable signal first):

1. Explicit content-type when supplied by the acquirer.
2. File extension.
3. Content sniffing (magic bytes) to confirm/override the extension.

Unsupported media (e.g. `moving_image` today) is **recorded and flagged** —
`supported=False`, with a reason in `notes`. The run does **not** fail; the
source is skipped by later stages.

### 3. Disposition classification (per page/section)

Classes: `structured` | `unstructured` | `image` | `map`.

Approach: **heuristic + config override** (deterministic, debuggable, no
per-page inference cost). This matches the operational reality of the OOB
extractor, which already relies on config overrides for messy scans.

Per-page heuristics (PDF, via `fitz`):

- **text density / char count** → prose (`unstructured`) vs. sparse.
- **table/column detection** (line rulings, aligned x-spans) → `structured`.
- **image-area fraction** (rendered image bbox coverage) → `image`.
- image-fraction high **and** map cues (scale bar, "Map" caption, large graphic
  with sparse overlaid text) → `map`.

Each classified region carries a **confidence** and a **review flag**. The fuzzy
middle (half-table/half-caption pages; maps with text legends) is handled by:
confidence + review flag + **config override**. Ambiguous regions are flagged
for a human; once decided, the decision is pinned in the override config so it
sticks. No ML classifier is required for four coarse classes.

Granularity: **per page / per section**, not pixel-level layout segmentation.

### 4. Routing manifest

Per page/section entry:

- `page_range` / `section_id`
- `disposition`
- `confidence`
- `needs_review` (bool)
- `provenance_anchor` (page number / section offset for citation)

The manifest + the source-metadata record are the front-end's only outputs.

### Stage-3 seam (the bug fix)

Add an entry point to the conversion stage that consumes the manifest and, per
region, dispatches to a disposition-specific converter:

- `unstructured` → existing `pymupdf4llm.to_markdown` on **that page range**.
- `structured`   → table-aware Markdown for that range. (This is where the OOB
  extractor's *normalization* knowledge — rank/date/unit cleanup — is relocated;
  it produces Markdown tables, consistent with "Markdown is the universal
  intermediate.")
- `image` → **new**: extract the image asset (`fitz` `get_pixmap` /
  `extract_image`), write it as a file, emit Markdown with an image reference +
  caption so `phase1_parse`'s existing `Image` slots populate.
- `map`   → same extraction, tagged as map → `Map` slots.

Existing whole-document behavior remains the default when no manifest is present,
so nothing currently working breaks. The region loop is the only new control
flow. This is what unblocks PDF image/map extraction: the downstream model is
unchanged; it is finally fed the data it was designed to accept.

## Non-goals

- No LLM/vision classifier (cost + non-determinism; heuristics suffice for 4
  classes).
- No pixel-level layout segmentation (page/section granularity is sufficient).
- No change to stage-4 entity extraction semantics.
- Structured-data relational load, ULID crosswalk to narrative
  `PersonID`/`PeopleGroupID`, and entity de-duplication are **out of scope** here
  (tracked separately; see `STRUCTURED_DATA_ROUTING.md`).

## Implementation status

- [x] Spec (this document)
- [ ] Step 1 — Source-metadata model (`src/ingestion/source_metadata.py`)
- [ ] Step 2 — Media-type detector (`src/ingestion/media_detection.py`)
- [ ] Step 3 — Per-page/section disposition classifier
- [ ] Step 4 — Routing manifest emitter
- [ ] Step 5 — Stage-3 seam + image/map extraction converter
- [ ] Step 6 — Verify image/map handler output end-to-end on the OOB PDF
- [ ] Later — relocate OOB table normalization into the `structured` converter
