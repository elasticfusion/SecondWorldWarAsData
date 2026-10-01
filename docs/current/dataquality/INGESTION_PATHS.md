# Ingestion Paths — Complete Routing Reference

How every uploaded file is routed from the S3 front door to a processing track,
with the success path and the failure/escalation path for each type. Reflects the
code as of 2026-10-01 (branch `feature/phase0-autotrigger-routing`).

Companion docs: `INGESTION_FRONT_END.md` (per-page disposition design),
`INTAKE_FRONT_DOOR_STATE.md` (deployed-state log), `MAP_IMAGE_AV_INGESTION.md`
(the vision branch design).

---

## Front door

Upload to `s3://{bucket}/contentrepository/...` → `s3:ObjectCreated:*` (prefix
`contentrepository/`, no suffix filter) → `{env}-wwii-content-uploaded` SNS →
`trigger_handler.handler`.

Two gates run before routing:
1. **`_content_keys`** — decides what is processable.
2. **`_split_by_media`** — routes processable keys to one of four tracks by extension.

### `_content_keys` (the door)
| Input | Decision |
|---|---|
| archive: `.zip .rar .7z .tar .tar.gz .tgz .gz` | **Ignored** — expected (pre-stage expands archives); not a failure, no alert. |
| recognized content (see tracks) | **Accepted** → `_split_by_media`. |
| anything else (`.xyz`, `.exe`, no ext, …) | **Reject-to-review** → `needs-review/unrecognized-type/{book}.json` + alert. (Previously a silent `Skipping non-content` drop — now surfaced.) |

Recognized content extensions (`_CONTENT_SUFFIXES`): `.md .pdf .jpg .jpeg .png
.tif .tiff .docx .epub .html .txt .mp4 .mkv .mov .webm .avi .m4v`.

---

## The four tracks (`_split_by_media`)

```
                     ┌─ .pdf + images(.jpg/.jpeg/.png/.tif/.tiff/.webp/.bmp) ─→ OCR track
contentrepository/ ──┤─ .epub .docx (.txt)                                   ─→ CONVERT track
upload              ├─ .mp4 .mkv .mov .webm .avi .m4v                        ─→ VIDEO track (own path)
                     └─ .md .txt .html                                        ─→ PARSE (text) track
```

All tracks converge on the same contract: write
`contentrepository/{book}/chapter{N}/chapter{N}-content.md` (+ `-meta.yaml`),
whose upload re-triggers the PARSE path → Phase 1 → 2 → dedup gate → Phase 3.

---

### 1. TEXT / PARSE track — `.md .txt .html`
- **Success:** queued to parse (Phase 1) directly → extract → dedup → enrich.
- **Failure:** malformed markdown surfaces as a Phase-1 parse error (logged).
  *(No dedicated reject-to-review for bad standalone markdown yet — minor gap.)*

### 2. CONVERT track — `.epub .docx` (`.txt` also accepted)
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

### 3. OCR track — `.pdf` + images (`.jpg .jpeg .png .tif .tiff .webp .bmp`)
`_submit_ocr` → Chandra GPU OCR (AWS Batch) → `ocr_merge_handler` promotes merged
markdown → parse.
- **Success:** merged markdown → `chapter1-content.md` → parse → …
- **Failure — media mismatch** at intake (`_ocr_media_mismatch`: ranged 4 KB
  header fetch + sniff; e.g. a `.pdf` that is html/zip): reject-to-review
  `needs-review/ocr-media-mismatch/` + alert, **before** a GPU job is spent.
  Fail-open: a transient read error proceeds to OCR (never block a legit doc).
- **Failure — empty OCR** (no markdown produced; previously a silent `return ""`):
  reject-to-review `needs-review/ocr-empty/`.
- **Failure — near-empty OCR** (< `OCR_MIN_USABLE_CHARS`=40 usable chars; e.g. a
  photo/map sent to OCR, or an unreadable scan): reject-to-review
  `needs-review/ocr-near-empty/`.
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

Principle: extension is a fast **guess**; routes that fail off-ramp to human
review (with an automated Grok-repair attempt first on the convert path) rather
than silently dropping or feeding garbage downstream. No silent failures.

---

## Design notes & known gaps

- **Born-digital PDFs** (text layer): currently go to Chandra like any PDF.
  Empirically the WWII corpus is scanned (NARA B-series = 0 text chars → Chandra
  required), and a born-digital PDF OCRs correctly (just slower). A PyMuPDF
  text-first path exists (`pdf_pipeline`, orphaned) and is validated to route an
  image-only/JPG-in-PDF correctly to Chandra; wiring it is an **efficiency option**,
  not required. Decision deferred (low payoff for a scanned corpus).
- **Photos / maps / diagrams → vision captioner (BUILT, opt-in).** Images from any
  source (standalone, embedded-in-PDF, Chandra `![...]`) land as `parsed['images']`.
  `src/extraction/image_captioner.ImageCaptioner` identifies (not OCRs) them via
  Grok vision using the surrounding context `images.py` links; populates
  `description` + classification + confidence; low-confidence/empty → `needs_review`;
  never fabricates. Config-gated `images.vision_caption` (default off). Distinct
  from the video track's speaker-id vision.
- **Standalone bad markdown → reject-to-review (BUILT).** A direct `.md/.txt/.html`
  upload with no usable content off-ramps to `needs-review/bad-markdown/` instead
  of a doomed parse (trigger `_validate_parse_keys`).
- **Structured-reference (OOB) routing (BUILT).** A doc under the reserved `_oob/`
  path (or a configured `ingestion.structured_stems`) is tagged structured at OCR
  intake (`.structured` marker); after Chandra OCR, `ocr_merge_handler` routes its
  markdown to the deterministic `phase0_ingest` OOB parser (`Phase0IngestTaskDef`),
  NOT narrative LLM extraction. Entity convergence OOB→people at dedup is the
  documented pending bridge.
- **Non-English documents → translation (BUILT, all tracks, opt-in).** Translation
  (`src/ingestion/translation.py`, design `LANGUAGE_TRANSLATION.md`: detect →
  Grok translate → English markdown at Phase 0, inert provenance marker) is now
  wired into **every** track via the shared `normalize_markdown_to_english`
  adapter (config-gated `ingestion.translate` / `PHASE0_TRANSLATE`; fail-safe —
  English/disabled/error passes through unchanged):
    - **OOB** (`phase0_ingest`): per-page, pre-existing.
    - **CONVERT** (`phase0_convert`): per-document, before writing chapter structure.
    - **OCR-narrative** (`ocr_merge_handler`): per-page (Chandra page separators),
      before narrative promote; the OOB fork is skipped (it translates in
      phase0_ingest).
    - **VIDEO** (`phase0_video`): **per-segment** — detect once on the joined
      transcript, translate each segment's text while preserving timecodes +
      speaker labels (only spoken text is sent to Grok).
  A non-English source (German KTB scan, German-language video, etc.) is now
  normalized to English BEFORE Phase 1, so the English-centric Phase 1–3 stack
  (prompts, `normalize_name_ascii`) runs unchanged. Enable via config when the
  corpus includes non-English material.
