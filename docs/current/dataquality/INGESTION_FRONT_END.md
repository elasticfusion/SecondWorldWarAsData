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

**Two regimes — native vs. scanned PDFs.** End-to-end validation on the real ETO
Order of Battle PDF (step 6) established that geometry heuristics only work on
*native* PDFs:

* **Native PDFs** (vector text + embedded raster images): per-page heuristics
  work — text density -> `unstructured`; detected tables (`find_tables`) ->
  `structured`; image-area fraction -> `image`; large image/graphic + sparse
  text (+ vector drawings) -> `map`.
* **Scanned PDFs** (every page a full-page scan image, usually with an OCR text
  layer): geometry **cannot** determine structure. On the OOB PDF every page has
  `image_area_fraction` ~1.0, `find_tables` finds nothing (no vector layer), and
  OCR span geometry does not separate tables from prose (a real command-staff
  roster page looked *less* columnar than the prose preface). So the classifier
  detects "scanned" up front (a dominant fraction of sampled pages are full-page
  images) and does **not** guess structure: text-bearing scanned pages ->
  `unstructured` + `needs_review`, with a note that structure is recovered from
  the **OCR+AI (Chandra) markdown**, not PDF geometry; near-empty-text scanned
  pages -> `image`. See "Scanned documents" below.

Per-page heuristics (native PDF, via `fitz`):

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

## Scanned documents (validated finding, step 6)

End-to-end validation on the real ETO Order of Battle PDF (602 pages) surfaced a
design limitation and set the direction for structured extraction from scans.

**Finding.** The OOB PDF is a fully-scanned document: every page is a full-page
raster scan with an embedded OCR text layer. Consequently none of the native
geometry signals discriminate structure:

* `image_area_fraction` is ~1.0 on every page (useless as a discriminator).
* `find_tables()` returns nothing (it needs a vector text layer).
* OCR span/line geometry does not separate tables from prose — measured on real
  pages, a command-staff roster looked *less* columnar than the prose preface.

**Decision (validated).** Structure for scanned documents is recovered from the
**OCR+AI (Chandra) markdown**, not from raw-PDF geometry. This was verified: the
per-division Chandra markdown (`ocr_output/*.md`, 54 files, ~9,180 table rows)
represents every OOB section as proper HTML tables with `rowspan` grouping, and
its cell content is *cleaner* than the current CSV pipeline (e.g. `William C Lee`
vs. the OCR-mangled `William C Leo`). Residual issues are minor and bounded
(occasional letter slips like `McLuliffe`; some empty cells) — not dropped or
hallucinated rows.

**What the front-end does for scanned docs (this step).** Detect "scanned" and
classify honestly: text-bearing pages -> `unstructured` + `needs_review` with a
note deferring structure to the markdown; blank-ish pages -> `image`. The
front-end no longer emits confidently-wrong `structured`/`image` labels on
scans. This is the correctness fix (Piece 1).

**Follow-up (Piece 2): markdown -> structured JSON.** A separate component will
parse the Chandra HTML tables into structured JSON (rowspan expansion, mapping
to the OOB schema, joining `source_page` provenance). Garble handling there
should be **verification/flagging, not LLM correction**: emit confidence +
`needs_review` for suspect cells rather than rewriting text (a confidently-wrong
correction is worse than visible garble for a citable reference). Actual
correction, if ever needed, should re-read the source *image* region (where the
model can see the pixels), and is a cost-gated optimization. The downstream
person identify/merge stage will incidentally repair some garbled names of
*well-attested* people via cross-source redundancy, but it will NOT rescue the
OOB's unique long-tail officers (no redundancy) or non-person fields (dates,
ranks, units), and can risk false merges — so it is a safety net, not the
primary strategy. Cleanliness comes from reading the good source (Chandra
markdown) and flagging the rest.

### Piece 2, increment 1 — command-and-staff parser (findings)

`src/ingestion/oob_markdown/command_staff.py` parses the COMMAND AND STAFF
succession tables. Validated on the full corpus (62 markdown files): **1,324
rows across 53 files**, with **27% flagged for review** (mostly unknown-division
rows, below). Findings that shaped the implementation:

* **Filenames are misaligned with content** — not merely unreliable. E.g.
  `1st_infantry.md` contains 14th/16th Armored data; `4th_armored.md` contains
  4th/5th Armored. Division identity is therefore tracked from in-content title
  lines (letter-spaced `## 1 0 1 st A I R B O R N E ...` H2s and inline
  `Nnn Infantry Division` lines), never the filename.
* **Leading title-less tables** — some files open with a COMMAND AND STAFF table
  before any in-content division title. These rows are **captured under the
  `(unknown)` division and flagged for review**, not dropped and not guessed
  (assigning a division from the misaligned filename would be a fabrication).
  363 rows fall in this bucket and await human division assignment.
* **Two division files** (`2nd_french_armored`, `71st_infantry`) yield no
  command-staff table (unrecognized structure or genuinely absent); noted for
  follow-up rather than silently ignored.
* **No abbreviations authority exists** in the repo, so rank/position/unit
  normalization is derived from the data itself (a rank-token vocabulary), not a
  lookup file. Rank+name are split from the single combined cell.
* **HTML parsing** uses BeautifulSoup (already a project dependency via the
  HyperWar importer), which handles the irregular markup (mixed
  `border`/`thead`/`tbody`, single-line tables, `<br/>`, `&amp;`).

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
- [x] Step 3 — Per-page/section disposition classifier
- [x] Step 4 — Routing manifest emitter
- [x] Step 5 — Stage-3 seam + image/map extraction converter
- [x] Step 6 — Verify image/map handler output end-to-end on the OOB PDF
      (validated; surfaced the scanned-document finding above; classifier made
      scanned-aware so it no longer mislabels scanned pages)
- [x] Piece 2 (increment 1) — Parse COMMAND AND STAFF markdown tables into
      structured rows (`src/ingestion/oob_markdown/`). BeautifulSoup-based;
      handles rowspan + empty-`<td>` position grouping, combined rank+name cell
      splitting, `(actg)` acting flags, `<br/>`/entity cleanup, and division
      tracking from content. Verification-flagging only (no correction): suspect
      cells get `needs_review` + `confidence` + `notes`, raw cell preserved.
- [ ] Piece 2 (increment 2+) — Remaining OOB sections (statistics, campaigns,
      organic units, attachments, detachments, higher-unit assignments, command
      posts) using the same framework.
- [ ] Later — relocate OOB table normalization into the `structured` converter
- [ ] Later — parser extension so local map assets populate the `Map` slot
      (currently emitted as embedded images; parser's map regex is URL-only)
- [ ] Later — resolve `(unknown)`-division command-staff rows and the two
      division files with no detected table (`2nd_french_armored`, `71st_infantry`)
