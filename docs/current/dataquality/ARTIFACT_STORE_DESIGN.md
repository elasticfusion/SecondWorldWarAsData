# Artifact / Document Store — Design (DRAFT, design-first)

Status: DRAFT — design under discussion with the owner. No code yet.
Last updated: 2026-10-14

Companion docs: `INGESTION_PATHS.md` (the existing S3 front door + four tracks),
`INGESTION_FRONT_END.md` (per-page disposition), `MAP_IMAGE_AV_INGESTION.md`
(vision branch), `URL_SOURCED_ENRICHMENT_DESIGN.md` (web fetch + registry),
`bibliography_resolution_process.md` (NARA/archive resolution).

---

## 1. Problem & vision

We acquire source documents from many origins and must process them once, store
them durably with provenance, and feed them back into the pipeline (Phase 1/2/3).
The concrete, **non-hypothetical** need is a **standard way to import a
document** regardless of where it came from. Origins include:

- Web pages (can be **KB-small** single-file snapshots), EPUBs, PDFs, and
  **objects behind links** (e.g. a book on archive.org reached via a Wikipedia
  or bibliography link).
- **NARA** — "just another source", available **online OR offline**: online via
  the NARA catalog/API, AND offline as bulk files already on disk (e.g. the
  `WWIIArchives` NARA RG238 M1019/M1270 microfilm rolls + finding aids). NARA is
  NOT online-only.
- **Bulk operator-supplied files** staged locally (e.g. `~/Downloads/WWIIArchives`:
  map imagery, NARA RG238 microfilm, FMS report series, finding-aid PDFs).

Artifacts span a huge size range — from a **few-KB** captured web page to
**multi-GB** scanned collections.

**Unifying principle (owner):** this is NOT several pipelines. It is ONE
document-import path: an artifact from any origin → relevance-validate →
process → store durably with provenance → feed Phase 1/2/3 → deep-archive the raw
master.

---

## 2. Locked decisions (owner)

1. **Deep-archive after initial processing (CONFIRMED).** Raw masters live in S3
   permanently; once processed (relevance + OCR/extract → fed Phase 1/2/3), the
   `original/` master transitions to **S3 Glacier Deep Archive** via a lifecycle
   rule. Process-once; rarely touched again. (Matches the project "never
   reprocess unchanged content" cost principle.) Applies across the size range
   (KB web snapshots to GB collections).
2. **One import path for all origins (CONFIRMED).** The real need is a STANDARD
   way to import documents. NARA = another source. Bulk-import = the general
   case.
   - **SOURCE is orthogonal to ORIGIN.** A *source* (NARA, archive.org,
     Wikipedia/bibliography link, bulk disk, ...) can arrive **online** (adapter
     downloads it first) OR **offline** (already staged on disk). NARA is both.
     The ONLY difference is whether the adapter prepends a (streaming) download
     step; everything after the artifact LANDS is identical.
3. **REJECT compressed files at the door (CONFIRMED).** `.zip .rar .7z .tar .gz
   .tgz` are **rejected to review** — the user decompresses before upload. So
   there is NO container enumeration, NO member recursion, and NO `.rar`/`.7z`
   dependencies anywhere. (Note: this CHANGES today's behavior, where archives
   are silently *ignored* at the door — we move them to an explicit reject +
   "please decompress and re-upload" message.)
4. **Relevance validation required (Grok, fail-closed)** before committing
   storage/processing.
5. **High-quality selection where "one best" is wanted** (e.g. a single unit
   patch image): choose the largest/highest-resolution vision-confirmed
   candidate.
6. **Do NOT compress artifacts (CONFIRMED).** Store `original/` as the
   byte-faithful master. Rationale: (a) the data is predominantly ALREADY
   compressed — scanned map/microfilm imagery (JPEG/JP2/TIFF), PDFs with embedded
   compressed images, and EPUB/DOCX/XLSX (themselves ZIP containers) — so
   re-compression yields near-zero gain on the bulk of the bytes; (b) the storage
   base is tiny — Glacier Deep Archive is ~$1/TB-mo, so ~1–2 TB is ~$1–2/mo and
   even a 50% reduction saves ~$1/mo; (c) compression adds a decompress step on
   every reprocess/serve (on top of already-slow Deep Archive retrieval), makes
   `original/` no longer byte-faithful, and complicates checksum/dedup. The
   immaterial savings do not justify the recurring complexity. (The small, truly
   compressible slice — plain txt/html/md/OCR — lives in `derived/`, not the
   archival master.)

### Deferred (not now)
- **Entity-linkage model (Joe-Smith UI)** — hypothetical for now; IGNORE. The
  store will carry provenance; linking a document to person/unit/place and the
  upload UI are a later concern, not part of the standard-import work.
- **Web-page archival format** (zip/WARC vs rendered snapshot) — a KB web capture
  is just a small file; the fancy archival-format question is deferred.

---

## 3. Grounding in what already exists (DO NOT reinvent)

The S3 **front door already exists** (`INGESTION_PATHS.md`):

- Upload to `s3://{bucket}/contentrepository/...` → `s3:ObjectCreated` →
  `{env}-wwii-content-uploaded` SNS → `trigger_handler.handler`.
- `_content_keys` gate → `_split_by_media` → four tracks (OCR / CONVERT / VIDEO /
  PARSE) → all converge on `chapter{N}-content.md` → re-trigger Phase 1→2→3.
- `_CONTENT_SUFFIXES` already covers `.pdf .epub .docx .html .txt` + images +
  video. **AV scanning** (ClamAV, demand-launched on Fargate, ~2.4 GB-capable)
  already gates binaries.
- **Archives (`.zip .rar .7z .tar .gz`) are currently IGNORED at the door.**
  Per decision #3 we change this to an explicit **reject-to-review** ("please
  decompress and re-upload").

So this effort is **mostly an EXTENSION of the existing front door + a durable
archival layer + acquisition adapters**, not a greenfield subsystem.

### What is genuinely NEW
1. **Durable archival storage + Glacier Deep Archive lifecycle** for raw masters
   (today `filestore/` is NOT even synced to S3 — equipment/map images written
   there are LOST when the ECS task exits; see §7).
2. **Relevance validation at acquisition** (Grok, type-appropriate, fail-closed).
3. **Explicit reject of compressed uploads** (replace the silent ignore).
4. **Online-acquisition adapters** (web/EPUB/PDF/object, NARA) that LAND files in
   the front door — streaming, multi-GB-safe (NOT the 5 MB-capped HTML
   `url_fetch`).

(No container-expansion work — removed per decision #3.)

---

## 4. Proposed S3 layout + lifecycle

```
s3://{bucket}/
  contentrepository/...        # EXISTING front door (processable units land here)
  output/...                   # EXISTING derived JSON (synced from ECS)
  artifacts/                   # NEW durable artifact store
    <source>/<artifact_id>/
      original/<file>          # archival master (→ Glacier Deep Archive after processing)
      derived/<markdown|ocr>   # processable output that feeds the front door
      manifest.json            # provenance + type + checksum + status + lineage
```

- **Tiering:** `original/` transitions to **Glacier Deep Archive** via an S3
  lifecycle rule once `manifest.status == processed`. `derived/` stays in
  Standard/IA (it is what gets reprocessed/served).
- **Artifact index:** `manifest.json` per artifact + a DynamoDB index for queries
  (by checksum, source id, status). Reuse the URL-registry concept
  (checksum/URL identity + dedup) for web-sourced ones.
  - **DynamoDB is an INDEX/dedup concern, NOT a retrieval cache.** S3 GET on
    Standard/IA (~tens of ms) is fast enough to retrieve the bytes directly — we
    do NOT front artifact retrieval with DynamoDB. DynamoDB earns its place only
    for things S3 can't answer efficiently: (a) **dedup on acquisition** ("have
    we already stored this URL/sha256?" — one GetItem vs an S3 scan), (b)
    **reverse lookup** ("which artifacts belong to entity X?"), (c) **status**
    ("which masters are processed → tag deep-archive"). These matter at **M1**
    (bulk import), not M0.
  - **M0 needs no DynamoDB:** the unit_patch is recorded directly on the group
    record (`unit_patch.{url,local_path,sha256,...}`), so the group JSON is the
    index for that one artifact, and the small patch stays in Standard (fast GET).
- **Re-ingestion loop:** promoting `derived/<...>.md` into
  `contentrepository/{book}/chapter{N}/...` re-triggers the EXISTING Phase
  1→2→3. No new pipeline.
- **No `extracted/`** tier — compressed inputs are rejected, so there are no
  container members to extract.

---

## 5. The one import path (all origins converge)

```
ORIGIN ADAPTERS                       COMMON PIPELINE
 OFFLINE (already staged):        ─┐
   local dir, bulk disk,           │
   offline NARA RG238 files       ─┤
 ONLINE (adapter downloads 1st):  ─┤→ reject if   →  relevance-validate (Grok, fail-closed, type-aware)
   web/epub/pdf/object URL,        │   compressed      │
   NARA catalog/API               ─┘   else land in    ├→ store original/ (+ checksum, provenance)
                                       artifacts/       ├→ process: OCR/convert → derived/ (reuse OCR/CONVERT tracks)
                                                        ├→ promote derived/ → contentrepository/ → Phase 1→2→3
                                                        └→ mark processed → lifecycle moves original/ to Deep Archive
```

Online vs offline differ ONLY in the download step; after an artifact lands in
`artifacts/.../original/`, the path is identical. The same SOURCE (e.g. NARA) may
use either adapter. Compressed files are rejected up front (user decompresses).

**Relevance validation by type** (large/binary can't be handed to Grok whole):
- text/HTML/short PDF → content-based verdict (reuse `url_content_verdict`).
- large PDF / microfilm scan → **metadata-based** verdict (finding-aid text,
  NARA series description, filename, a sampled page/first-N-pages).
- image (e.g. unit patch) → **Grok vision** verdict.

---

## 5a. Phase-3-discovered source material: ENRICH vs CAPTURE

When Phase 3 discovers people_groups source material — e.g. a website dedicated to
the 82nd Airborne in WWII, or a unit-movement history for the Bulge/Normandy — do we
CAPTURE the source material for processing, or just reference it? Today we only do the
former-weak case: `web_results`/`veterans_associations` run through
`_summarize_url_page` → a 1-2 sentence summary + `{url,title,summary}` on the group
record. The page content is NOT durably stored and does NOT re-enter Phase 1/2/3. A
rich 82nd-Airborne movement history is reduced to one sentence + a link.

**Two different intents on the same discovered URL:**
- **ENRICH (exists):** "summarize this page to annotate the group record." Cheap, a
  snippet, not re-ingested. Right for a stub bio / thin reference page.
- **CAPTURE (M1):** "this is substantive primary/secondary material — store it durably
  and PROCESS it for entities." The discovered URL becomes an ONLINE-origin artifact:
  Grok relevance gate → store `original/` → OCR/convert → `derived/` → promote to the
  front door → Phase 1/2/3 extracts events (movements), dates, places, people → all
  linked to the discovering group's `GroupID`. Deep-archive the master. This is the raw
  material for the future "map of where the unit was" (the unit-level analogue of the
  Joe-Smith map).

**Decision needed → a capture-worthiness gate.** Not every `web_results` URL deserves
full capture+processing. Add a *capture-worthiness* axis to the Grok content-verdict
(`url_content_verdict` already judges relevance / fact-confirmed / opinion): "is this
substantive source material worth capturing-and-processing, or just a reference to
summarize?" Only capture-worthy sources enter the CAPTURE path; the rest stay ENRICH
snippets. (OWNER INPUT WANTED on the threshold — see §7.)

**Websites are multi-page sources,** not single documents — capture may enumerate
in-scope pages (same-domain, WWII-82nd-relevant) rather than one URL. Scope of
crawl/enumeration is an M1 open question.

---

## 6. Milestones (agreed sequence)

**M0 (tiny, first) — unit-patch image.** End-to-end proof of the image sub-path
on a trivial artifact: run the patch/insignia queries → **Grok vision** verify
each candidate as a unit patch (fail-closed) → keep the SINGLE largest
vision-confirmed image → store under the new store (synced prefix) → record on
the people_groups record. Proves download + vision + durable-store on one small
file. Separate reviewed PR + tests + schema bump.

**M1 — standard document importer + durable store (the `WWIIArchives` case).**
The cataloguer/importer that walks a staging set (local dir OR a provided URL
list), **rejects compressed files**, computes checksums, detects type, writes
`artifacts/<source>/<id>/original/` + `manifest.json` with provenance, and
hands processable types to the existing OCR/CONVERT tracks → `derived/` →
front door. Forces the storage/provenance/tiering model to be real. The initial
TB-scale WWIIArchives load is likely a **one-time operator `aws s3 sync`** into
`artifacts/.../original/` (NOT through ECS ephemeral disk) + a cataloguing pass.

**Then:** online acquisition adapters (web/EPUB/PDF/object + NARA) — the SAME
path with a streaming download in front. **This includes Phase-3-discovered source
material (§5a):** a capture-worthy `web_results`/`veterans_associations` URL (e.g. an
82nd-Airborne movement history) is an online-origin artifact captured + processed +
linked to the discovering `GroupID`.

---

## 7. Open questions / risks to settle BEFORE code

1. **Scale & cost (budget sign-off).** WWIIArchives is ~1–2 TB. Decision #1
   confirms raw masters live in S3 with Glacier Deep Archive for `original/`;
   remaining item is just the explicit **budget acknowledgment** for ~1–2 TB
   (Deep Archive is ~$1/TB/mo storage, but retrieval is slow + costed). No
   architecture question left.
2. **ECS ephemeral disk vs multi-GB files.** Tasks can't buffer 7 GB files.
   Online adapters need streaming multipart upload; the initial local bulk load
   should be a one-time operator `aws s3 sync` into `artifacts/.../original/`
   rather than through ECS.
3. **`filestore/` is not synced to S3 (DATA LOSS today).** Equipment/map images
   written to `filestore/` never reach S3. The new store must live under a synced
   prefix (`artifacts/` added to the sync, or under `output/`). M0 must pick the
   synced location. Migrating equipment/maps off `filestore/` is a follow-up.
4. **Capture-worthiness threshold (§5a).** For a Phase-3-discovered URL, when is it
   substantive enough to CAPTURE+process vs just ENRICH-summarize? Owner input wanted:
   a Grok capture-worthiness verdict (default conservative — only clearly substantive
   primary/secondary material), vs a size/structure heuristic. False-capture wastes
   OCR + re-ingestion cost; false-skip loses a unit history.
5. **Website crawl scope (§5a).** A unit-dedicated website is multi-page. Capture one
   discovered URL only, or enumerate same-domain in-scope pages (and how deep)? Politeness
   + the URL registry (dedup, per-domain cooldown) already exist to bound this.
4. **Reject-compressed UX.** Confirm the reject message/alert wording ("archive
   rejected — please decompress and upload the individual files"), and that this
   intentionally supersedes today's silent ignore.

### Resolved / removed
- ~~Container formats (.rar/.7z deps)~~ — REMOVED (compressed rejected).
- ~~Entity-linkage schema~~ — DEFERRED (hypothetical; provenance only for now).
- ~~Web-page archival format~~ — DEFERRED (KB capture is just a small file).
- ~~Relevance granularity for containers~~ — REMOVED (no containers).

---

## 8. Explicitly NOT doing (yet)

- No new parallel pipeline — reuse the `contentrepository/` front door + Phase
  1/2/3.
- No container/compressed-archive expansion — rejected at the door.
- No entity-linkage schema / upload UI — deferred.
- Code starts at M0 (unit patch) once §7 Q3 (which synced prefix) is picked;
  M1 after.
