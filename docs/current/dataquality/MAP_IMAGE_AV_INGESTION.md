# Map, Image & Moving-Image Ingestion — Design Note

**Status:** proposed (2026-09-26). Assessment + probes done on the SHAEF OB map
corpus; **no pipeline built or run yet** (all work so far was throwaway tests
per the "treat OCR as tests" guidance). This note records the recommended
handling before any build.

## Scope

Three new source classes that do **not** fit the printed-text OCR path
(Chandra) or the narrative LLM-extraction path:

1. **Situation / operational maps** — e.g. `SHAEF OB Maps` (183 JPEGs,
   ~6,700×9,000 px, ~2 GB, one per day Oct 1944–Apr 1945).
2. **Photographs / still images** — scanned or born-digital.
3. **Moving images** — film/video with optional audio narration.

Plus the **NARA situation-maps guide PDF** (a born-digital finding aid with a
full text layer) — handled by the *existing* text path, not treated as a map.

## Core principle: disposition-based routing, deterministic-first

Phase 0 already classifies media type + disposition. Extend the router so
**"OCR" is only one branch**, and every branch is preceded by a **deterministic
funnel** that minimizes/eliminates paid LLM (Grok) usage:

```
                       ┌─ text layer present ─────────→ existing text converter
  source ─ disposition ┼─ scanned printed text ───────→ Chandra OCR
                       ┼─ photo / map / diagram ──────→ VISION branch (below)
                       └─ moving image ───────────────→ keyframes + transcription
```

### The deterministic funnel (run BEFORE any Grok/vision call)

Measured on the SHAEF corpus; each step is pure CPU / free:

1. **Filename + path parse** — structured metadata for free. SHAEF filenames are
   `DDMMYY SHAEF OB.jpg` inside `YY-MM` folders. **Use the folder for
   year+month (truth) and the filename for the day**; a naive filename-only
   `YY→19YY` read mis-dates the Feb-1945 (`...0245`) files. Yields a compile
   date for **183/183** maps, range 1944-10-01 → 1945-04-30.
2. **Calendar + sequence validation** — caught two data-quality issues for free:
   `311144` (31 November does not exist → mislabeled) and a **missing 15 Feb**
   (Feb folder has 29 files, gap detectable).
3. **EXIF / file metadata** — capture date, dimensions, DPI deterministically.
4. **Perceptual hashing (pHash/dHash)** — dedup near-identical frames/maps and
   detect day-to-day change in the time-series; skip re-analysis of unchanged
   content (matches the "never re-embed unchanged content" cost rule).
5. **Crop-to-ROI** — never send a 60-MP image to a model; send only the needed
   region (legend for dates, a tile for a sector). Probes here used ~0.2–0.8 MB
   crops instead of the 9–14 MB originals.
6. **Local OCR (PaddleOCR)** — the project already ships PaddleOCR in the Paddle
   Docker image (`src/ingestion/paddle_structure.py`). Stenciled block text on
   maps (army labels, scale bars, any printed date) should go through local OCR
   **before** any Grok call; Grok/vision only as a low-confidence fallback.

**Grok/vision is reserved for the genuinely visual-semantic residual**:
interpreting unit symbols, tracing the front line, image captioning — and even
then on crops/tiles, not whole images.

## Empirical finding: dating needs (almost) no Grok

A Grok-vision probe for the printed "LINE AS OF ⟨date⟩" legend returned
**null on 5/5** mid-month maps (Oct/Nov/Jan/Mar/Apr); wider-strip visual
verification confirmed the stamp is genuinely **absent on most maps** (present
on the 1 Oct map: "LINE AS OF 29 SEPT", i.e. ~2 days behind the compile date).

**Conclusion:** the deterministic filename date **is** the authoritative
per-map date. Do **not** budget Grok per-map for dating — it was measured to add
nothing on the majority. Model:

- `compile_date` = filename+folder parse (authoritative, 100% coverage).
- `operations_date` = the printed "LINE AS OF" date when present (override);
  otherwise `= compile_date` with a documented `−1..−2 day` note and a
  `date_source` flag (`printed_stamp` | `filename_inferred`).
- Anomalies (`311144`) → human/vision review, not bulk Grok.

## Goal 2 — units as map metadata ("where was the 3rd Armored on 15 Dec 1944?")

**Feasible.** At full resolution the symbology is clear NATO-style echelon:
- Small black flags with a number = **divisions** (`82`=82nd Abn, `101`, `104`…).
- Flags with an internal oval/tank glyph = **armored divisions** (`3`, `7`…).
- Corps/armies are printed text (`US VII CORPS`, `BR XXX CORPS`, `FIRST US ARMY`).
- Red text = **enemy** strength/formations ("NINETEENTH ARMY / 4 Inf. Divs.").

Extraction approach (vision, on tiles):
- Tile each map (labels are only legible near full res), run a vision pass per
  tile with a strict prompt → list of `{unit_id, echelon, parent_corps, side,
  tile_xy}`; merge tiles; dedup.
- Emit per map a **unit roster** → link to the existing **Military Units**,
  **Dates**, and **Maps/Image** entities. This alone answers the query:
  *unit + date → the map(s) that show it*, served as a **graphical supplement**
  (display the map image). This is the recommended first deliverable.
- Cost control: run vision **once per map** (cache), skip maps unchanged by pHash
  from the prior day, and only re-tile where change is detected.

**Hard caveat — corpus gap:** there is **no `44-12` folder**; **December 1944
is entirely missing**. The example query "3rd Armored on 15 Dec 1944" is
**unanswerable from this set** (the Bulge month is absent). Coverage is
Oct–Nov 1944 and Jan–Apr 1945. This gap must be surfaced to users (return
"no map for that date" rather than a wrong neighbor).

## Goal 3 — geolocation from the map (tertiary)

**Feasible but harder; phase it after Goal 2.** Unit flags sit at real frontage
positions, so a pixel location exists for each. Turning pixel→lat/lon needs a
**georeference** of each map sheet:
- Establish control points from **known labeled cities** (Paris, Aachen, Metz,
  Nancy, Cologne, Strasbourg, Bastogne — all printed on the maps) whose true
  coordinates are known → fit an affine/polynomial transform (pixel→geo) per
  sheet. This is standard GIS georeferencing; cities double as control points
  and as a QA check.
- Then each unit flag's pixel → approximate lat/lon (with an error radius) →
  the existing **geocoder cascade / PostGIS `geom`**, flagged `geo_review`
  (approximate) consistent with the project's provenance discipline.
- Deterministic assist: the printed **scale bar** (0–100 MILES, seen bottom-left)
  calibrates distance; city label detection can be local-OCR + template, not
  Grok.

Recommended sequencing: **Goal 2 (unit roster + map-as-supplement) first**
— high value, moderate effort, directly answers the user query — then **Goal 3
(georeference)** as an enrichment layer.

## Photographs & moving images (generalization)

- **Photos:** deterministic metadata + pHash dedup, then a single vision caption
  + entity-tag pass → **Image/Maps** entity with `description` (fills the
  steering doc's empty-caption gap). Grok once per image, cached.
- **Moving images:** deterministic keyframe sampling (scene-change detection) →
  per-keyframe vision pass (same as photos); audio → transcription (Whisper /
  existing transcription path) → text branch. **Timecode is the provenance
  analog of page number.** pHash across keyframes avoids re-analyzing static
  footage.

## Storage / query fit

All branches emit the same typed entities + provenance into the confirmed
**Postgres + pgvector single store** (see `SCHEMA_DESIGN_rag_store_tradeoff.md`).
Goal-2 answers are a structured join (unit × date → Maps/Image); Goal-3 adds
PostGIS geo. Map images themselves are served from S3/CDN as the graphical
supplement, referenced by `ImageID`.

## Recommended next steps (each a bounded, low-cost step)

1. Persist the deterministic date table into the real pipeline (no Grok).
2. Prototype the **Goal-2 unit-roster** vision pass on a **few tiled maps**
   (throwaway test) to measure unit-read accuracy + per-map cost before build.
3. Defer Goal-3 georeferencing to an enrichment phase.
4. Surface the **Dec-1944 gap** and the `311144` mislabel as data-quality items.
