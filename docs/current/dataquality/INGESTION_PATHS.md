# Ingestion Paths — Complete Routing Reference

How every uploaded file is routed from the S3 front door to a processing track,
with the success path and the failure/escalation path for each type. Reflects the
code as of 2026-11-16 (originally written 2026-10-01; corrected against `lambda_handlers/trigger_handler.py`, `ocr_merge_handler.py`, `src/ingestion/av_scan.py`).

Companion docs: `INGESTION_FRONT_END.md` (per-page disposition design),
`MAP_IMAGE_AV_INGESTION.md` (the vision branch design), `AV_SCANNING_DESIGN.md`,
`CHANDRA_OCR_DESIGN.md`. What happens to a document after routing, and how to resolve each
`needs-review/` category: [../core/DOC_LIFECYCLE.md](../core/DOC_LIFECYCLE.md).

---

## Front door

Upload to `s3://{bucket}/contentrepository/...` → `s3:ObjectCreated:*` (prefix
`contentrepository/`, no suffix filter) → `{env}-wwii-content-uploaded` SNS →
`trigger_handler.handler`.

Before routing, the handler also enforces book identity (slug rules in `src/ingestion/book_slug.py`;
the slug is immutable): an upload with no safe name goes to `needs-review/unnamed-upload/`, a document
already ingested under another slug (`source#<checksum>`) goes to `needs-review/source-renamed/`, and a
title collision is resolved by `title_uniqueness` (`_enforce_title_uniqueness_at_launch`). Derived
`_figures/` objects and metadata sidecars are ignored by `_content_keys`.

Two gates run before routing:
1. **`_content_keys`** — decides what is processable.
2. **`_split_by_media`** — routes processable keys to one of four tracks by extension.

### `_content_keys` (the door)
| Input | Decision |
|---|---|
| archive (`_COMPRESSED_SUFFIXES`: `.zip .rar .7z .tar .tar.gz .tgz .gz .bz2 .tar.bz2 .xz .tar.xz .z .lz .lzma .cab .arj`) | **Ignored** — expected (pre-stage expands archives); not a failure, no alert. |
| recognized content (see tracks) | **Accepted** → `_split_by_media`. |
| anything else (`.xyz`, `.exe`, no ext, …) | **Reject-to-review** → `needs-review/unrecognized-type/{book}.json` + alert. (Previously a silent `Skipping non-content` drop — now surfaced.) |

Recognized content extensions (`_CONTENT_SUFFIXES`): `.md .pdf .jpg .jpeg .png
.tif .tiff .docx .epub .html .txt .mp4 .mkv .mov .webm .avi .m4v`. `.webp`, `.bmp`, `.htm` and `.xhtml`
are listed in the track suffix sets but are NOT in `_CONTENT_SUFFIXES`, so `_content_keys` rejects them
as `unrecognized-type` before `_split_by_media` sees them.

---

## The four tracks (`_split_by_media`)

```
                     ┌─ .pdf + images(.jpg/.jpeg/.png/.tif/.tiff)  ─→ OCR track
contentrepository/ ──┤─ .epub .docx .html                          ─→ CONVERT track
upload              ├─ .mp4 .mkv .mov .webm .avi .m4v             ─→ VIDEO track (own path)
                     └─ .md .txt                                   ─→ PARSE (text) track
```

All tracks converge on the same contract: write
`contentrepository/{book}/chapter{N}/chapter{N}-content.md` (+ `-meta.yaml`),
whose upload re-triggers the PARSE path → Phase 1 → 2 → dedup gate → Phase 3.

---

## AV gate — when ClamAV is invoked (and when not)

After `_split_by_media`, before any track's processor touches the bytes, the
**AV gate** (`_av_gate` → `src/ingestion/av_scan.scan_and_gate`) runs. It is
**demand-launched and binary-only**: the ClamAV scan container
(`AvScanTaskDef`, a *separate* image from the pipeline) is launched **only when
the upload batch contains a binary**, over those binary keys. Controlled by
`AV_SCAN_ENABLED`, which defaults to **true**; `_enforce_av_gate_policy` refuses to start the handler
if it is false outside dev/local/test.

### Invoked (scanned)
| Track | Types | Why scanned |
|---|---|---|
| OCR | `.pdf`, images `.jpg/.jpeg/.png/.tif/.tiff/.bmp/.gif/.webp` | PDF → PyMuPDF/`fitz`; images → torch/Chandra — native parsers, real malware vectors. |
| CONVERT | `.epub .docx .doc .xlsx .xlsm .xls .pptx .ppt .rtf` | pandoc/office parsers; macros/OLE in the container. |
| VIDEO | `.mp4 .mkv .mov .avi .webm .m4v` (+ `.mp3 .wav .m4a`) | ffmpeg — many CVEs. Fargate scanner handles the ~2.4 GB files (no Lambda size cliff). |
| (any) | `.zip` | unpacked downstream. |

- **Clean** → the key proceeds to its track.
- **Infected** → **quarantine** (moved to `quarantine/{key}`, *not* deleted) +
  reject-to-review `needs-review/av-infected/` + alert + **freeze-submitter hook**.
- **Unscannable** (scanner outage/error/timeout, or a binary the scanner didn't
  report on) → **fail-closed**: quarantined-for-review, never advanced unscanned.

### NOT invoked (skipped) — and the underlying assumptions
| Input | AV? | Underlying assumption (why skipping is safe) |
|---|---|---|
| `.txt`, `.md`, `.markdown` | **No** | Inert data. We **parse** it / send it to Grok **as text** — we never render it in a browser or execute it. The only ways text "carries a virus" (browser-rendered `<script>`; a byte-polyglot fed to another interpreter) **do not occur in this pipeline**. A giant text "bomb" is a DoS/resource concern, already bounded by the empty/near-empty + size checks — not malware. |
| `.html` | **No** (parser-hardening, not AV) | `<script>`/handlers/data-URIs are dangerous only when **rendered in a browser or fetched by a tool**; we send HTML to the boilerplate stripper + pandoc (CONVERT track), not a browser, so active content is inert. Residual risk is pandoc/parser-side (crafted HTML → SSRF/file-read), addressed by parser hardening, not virus signatures. |
| text-only batch (no binary present) | **No launch** | The scan container is never started → **zero AV cost** for the common text-only upload. Gate: launch iff `ocr_keys ∪ convert_keys ∪ video_keys` is non-empty. |
| any type when `AV_SCAN_ENABLED=false` | **No** | Allowed only in dev/local/test (the handler refuses to start otherwise). The media-mismatch byte-sniff check still runs. |

Full design, threat model (incl. the pandoc-macro analysis), ClamAV placement
(separate container, Fargate), quarantine-not-delete, and the freeze-submitter /
identity-provenance requirement: **`AV_SCANNING_DESIGN.md`**.

---

### 1. TEXT / PARSE track — `.md .txt`
- **Success:** queued to parse (Phase 1) directly → extract → dedup → enrich.
- **Failure:** malformed markdown surfaces as a Phase-1 parse error (logged).
  *(No dedicated reject-to-review for bad standalone markdown yet — minor gap.)*

### 2. CONVERT track — `.epub .docx .html`
`_submit_convert` → Phase-0 convert ECS task (`phase0_convert.py`, pandoc via
`text_converters.convert_to_markdown`).
- **Success:** → chapter markdown → parse → …
- **Failure — media mismatch** (declared ext ≠ sniffed bytes, e.g. a `.epub`
  that is really a zip): reject-to-review `needs-review/media-mismatch/` + alert,
  before pandoc runs.
- **Failure — converter error** (pandoc choked): **second pass** —
  `md_correction.correct_markdown` asks Grok to repair the raw extracted text
  into clean markdown (reformat-only, verbatim, never fabricate).
    - Grok recovers usable markdown → continue with it (promote → parse).
    - Grok cannot recover → reject-to-review `needs-review/convert-failed/` +
      alert (human review).

> Note: pandoc has **no PDF reader** — PDFs never use this track.

### 3. OCR track — `.pdf` + images (`.jpg .jpeg .png .tif .tiff`)
`_submit_ocr` → per-book claim (`ocr#<slug>`) → `.chunks.json` manifest written → one Chandra GPU
Batch job per 50-page chunk (`OCR_CHUNK_PAGES`) → `ocr_merge_handler` waits for every chunk, then
`ocr_promote.promote` (sanitise, translate if enabled, split into chapters, cross-check against the
upload's `validation-url` metadata when present via the `phase0-crosscheck` task, normalise) → parse.
See `CHANDRA_OCR_DESIGN.md`. A doc under `_oob/` is tagged `.structured` and routed to the OOB parser
instead of the chapter path.
- **Success:** `contentrepository/<slug>/chapterN/chapterN-{meta.yaml,content.md}` → parse → …
- **Failure — media mismatch** at intake (`_ocr_media_mismatch`: ranged 4 KB
  header fetch + sniff; e.g. a `.pdf` that is html/zip): reject-to-review
  `needs-review/ocr-media-mismatch/` + alert, **before** a GPU job is spent.
  Fail-open: a transient read error proceeds to OCR (never block a legit doc).
- **Failure — empty OCR** (no markdown produced): reject-to-review `needs-review/ocr-empty/`
  (raised in `ocr_merge_handler.merge_ocr_output`).
- **Failure — near-empty OCR** (< `OCR_MIN_USABLE_CHARS`=40 usable chars; e.g. a
  photo/map sent to OCR, or an unreadable scan): reject-to-review
  `needs-review/ocr-near-empty/` (same function).
- **Failure — CUDA OOM** (prior work): flagged needs-review via the controller.

### 4. VIDEO track — `.mp4 .mkv .mov .webm .avi .m4v` — SELF-CONTAINED
`_submit_video` → separate `phase0_video.py` ECS task (own container): ffmpeg
→ Grok STT (diarized) → tiered speaker-id (9a/9b/9c, its own `GrokVisionAnalyzer`
for frames) → transcript markdown → parse.
- **Success:** chapter markdown (791-segment transcript proven on the Bulge doc)
  → parse → …
- **Failure:** `ANOMALY [video-processing-failed]` alert; **source video retained
  in S3** for re-processing (never deleted on failure).
- **Boundary:** video is the ONLY track with its own vision; its frames never
  become `parsed['images']`, so the document-image vision branch (below) never
  touches video. Do not route video through OCR/convert/text.

---

## Reject-to-review (the net)

All off-ramps use the shared `src/ingestion/review_reject.reject_to_review`:
writes `needs-review/{category}/{book}.json` (deliberately NOT under
`contentrepository/`, so a rejected file never re-triggers parse) and publishes an
operator alert to the `{env}-wwii-phase2-complete` SNS topic (email + Slack).
Best-effort: a marker/alert failure is logged, never raised — rejecting must not
crash the handler.

Categories in use:
| Category | Raised when |
|---|---|
| `unrecognized-type` | upload extension not in `_CONTENT_SUFFIXES` (and not an archive) |
| `media-mismatch` | convert track: declared ext ≠ sniffed bytes |
| `ocr-media-mismatch` | OCR track: declared ext ≠ sniffed bytes (pre-GPU) |
| `ocr-empty` | OCR produced no markdown |
| `ocr-near-empty` | OCR produced < 40 usable chars |
| `convert-failed` | pandoc failed AND Grok MD-correction could not recover |
| `convert-error` | the convert task raised (`phase0_convert`, fail-safe off-ramp) |
| `bad-markdown` | a direct `.md/.txt` upload with fewer than `PARSE_MIN_USABLE_CHARS` (20) usable characters (`_validate_parse_keys`) |
| `av-infected` | ClamAV found a signature (or the file was unscannable) |
| `unnamed-upload` / `source-renamed` | no safe slug / already ingested under another slug |
| `duplicate` / `title-collision` / `model-unavailable` | title-sameness check at launch (`_resolve_title_identity`): same work already ingested, same title but a different work, or the Grok model unavailable |
| `metadata` | raised in `src/ingestion/review_reject.py` (source-metadata completeness) |

Principle: extension is a fast **guess**; routes that fail off-ramp to human
review (with an automated Grok-repair attempt first on the convert path) rather
than silently dropping or feeding garbage downstream. No silent failures.

---

## Design notes & known gaps

- **Born-digital PDFs** (text layer): currently go to Chandra like any PDF.
  Empirically the WWII corpus is scanned (NARA B-series = 0 text chars → Chandra
  required), and a born-digital PDF OCRs correctly (just slower). A PyMuPDF
  text-first path exists (`src/ingestion/pdf_pipeline`, used by `phase0_ingest`, not by the trigger's OCR route) and is validated to route an
  image-only/JPG-in-PDF correctly to Chandra; wiring it is an **efficiency option**,
  not required. Decision deferred (low payoff for a scanned corpus).
- **Photos / maps / diagrams → vision captioner (BUILT, opt-in).** Images from any
  source (standalone, embedded-in-PDF, Chandra `![...]`) land as `parsed['images']`.
  `src/extraction/image_captioner.ImageCaptioner` identifies (not OCRs) them via
  Grok vision using the surrounding context `images.py` links; populates
  `description` + classification + confidence; low-confidence/empty → `needs_review`;
  never fabricates. Config-gated `images.vision_caption`. Distinct
  from the video track's speaker-id vision.
- **Standalone bad markdown → reject-to-review (BUILT).** A direct `.md/.txt`
  upload with no usable content off-ramps to `needs-review/bad-markdown/` instead
  of a doomed parse (trigger `_validate_parse_keys`).
- **Structured-reference (OOB) routing (BUILT).** A doc under the reserved `_oob/`
  path (or a configured `ingestion.structured_stems`) is tagged structured at OCR
  intake (`.structured` marker); after Chandra OCR, `ocr_merge_handler` routes its
  markdown to the deterministic `phase0_ingest` OOB parser (`Phase0IngestTaskDef`),
  NOT narrative LLM extraction. Entity convergence OOB→people at dedup is the
  documented pending bridge.
- **Non-English documents → translation (BUILT, all tracks, ON by default; `ingestion.translate: false` / `PHASE0_TRANSLATE=0` turns it off; clearly English text needs no Grok call).** Translation
  (`src/ingestion/translation.py`, design `LANGUAGE_TRANSLATION.md`: detect →
  Grok translate → English markdown at Phase 0, inert provenance marker) is now
  wired into **every** track via the shared `normalize_markdown_to_english`
  adapter (config-gated `ingestion.translate` / `PHASE0_TRANSLATE`; fail-safe —
  English/disabled/error passes through unchanged):
    - **OOB** (`phase0_ingest`): per-page, pre-existing.
    - **CONVERT** (`phase0_convert`): per section (at most 4,000 characters; mixed-language documents), before writing chapter structure.
    - **OCR-narrative** (`ocr_promote.promote`, from `ocr_merge_handler` or the cross-check task):
      per-page (Chandra page separators), after sanitising and before chapter splitting; the OOB fork
      is skipped (it translates in phase0_ingest).
    - **VIDEO** (`phase0_video`): **per-segment** — detect once on the joined
      transcript, translate each segment's text while preserving timecodes +
      speaker labels (only spoken text is sent to Grok).
  A non-English source (German KTB scan, German-language video, etc.) is now
  normalized to English BEFORE Phase 1, so the English-centric Phase 1–3 stack
  (prompts, `normalize_name_ascii`) runs unchanged. Enable via config when the
  corpus includes non-English material.
