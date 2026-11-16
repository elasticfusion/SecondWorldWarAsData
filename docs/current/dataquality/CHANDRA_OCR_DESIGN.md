# Chandra OCR: GPU-Accelerated PDF Processing

Design document for deploying Chandra OCR on AWS with GPU instances for high-quality PDF-to-markdown conversion.

**Status:** Deployed (AWS Batch GPU, `{env}-wwii-chandra` job def / `{env}-wwii-chandra-gpu` queue, plus an on-demand fallback queue). The "Operational findings" below were validated 2026-09-20; the architecture, Batch, image and workflow sections were brought in line with the code on 2026-11-16 (what changed is marked) | **Last Updated:** 2026-11-16

---

## Overview

[Chandra](https://github.com/datalab-to/chandra) is a vision-language model (VLM) that converts PDF pages to structured markdown, including accurate table extraction with HTML formatting. It outperforms traditional OCR for military documents with complex tables (order of battle, unit rosters, statistics).

### Local Test Results

Tested on `ETO_Order_of_Battle.pdf` (33MB, pages 21-34):
- Produced clean markdown with properly structured HTML tables
- Correctly extracted names, ranks, dates, unit designations
- Ran on CPU in ~15 minutes for 14 pages (no GPU available locally)
- With GPU: estimated 2-5 minutes for the same pages

---

## Operational findings (validated 2026-09-20)

Established by an edge-case probe on `St. Vith — The 7th Armored Division in
the Battle of the Bulge` (Boyer), a fully-scanned 252-page PDF, run on the
deployed Batch GPU pipeline. Six pages were chosen to exercise distinct
content types (map, block quote, rotated photo, footnotes, vertical table,
horizontal table). Outputs live under
`s3://dev-wwii-data-pipeline/ocr-output/stvith-probe/`.

### `--page-range` is 0-based — submit `N-1` for physical page `N`

The `chandra` CLI's `--page-range` argument is **0-based**, while the cover is
physical page 1. Passing `--page-range 12-12` returns the content of physical
page **13**. This was confirmed against local `pdftoppm` renders (physical
page 1 = cover; page 8 = a hand-drawn dispositions map; page 12 = the
INTRODUCTION containing the block quote).

**Rule:** to OCR intended physical page `N`, submit `--page-range (N-1)-(N-1)`.
The `chandra_entrypoint.sh` forwards the arg verbatim, so this offset is inherent to
`chandra` itself, not the wrapper.

**Now handled in code (2026-11-16):** every submitter converts 1-based physical pages to
Chandra's 0-based range through the one function
`src/ingestion/ocr_chunk_manifest.chandra_page_range` (used by
`trigger_handler._ocr_jobs` for automatic OCR and by `scripts/submit_ocr_job._to_chandra_range`).
Chunk directory names (`chunk-p0001-0050`) and the chunk-manifest stay 1-based. A page spec that
cannot be converted (`0`, `5-3`) raises instead of being sent 1-based.

### Chandra 2 emits image artifacts + captions on scanned pages

For a page whose content is a **photograph** (probe page 17, a rotated
combat photo), Chandra 2 emits `total_images: 1`, saves the cropped image as a
`.webp` artifact, and writes a natural-language caption/description into the
markdown (`![...](..._img.webp)` + descriptive text). It also auto-rotated the
sideways photo without prompting. For a **hand-drawn map** (page 8) it produced
a labelled transcription plus a natural-language description ("A hand-drawn map
showing the dispositions ... oriented with the top of the page to the left").

Implication for media binding (see INGESTION_FRONT_END.md): for **scanned**
pages the image artifact + caption can be taken from Chandra's own output,
rather than relying solely on `fitz` extraction. The binding requirement — the
actual image file attached to the markdown reference, not just described — is
satisfiable directly from Chandra output for scanned sources.

**What was built (2026-11-16):** the model-written caption is NOT kept as book text.
`src/ingestion/chandra_markdown.py` removes every picture paragraph (`![...](...)` plus Chandra's
description) from chapter text and lists it (file, PDF page); the book's own printed caption stays.
Phase 1 then builds the book's figures once from the PDF plus Chandra's crops
(`src/ingestion/book_figures.py` + `pdf_figures.py` -> `contentrepository/<slug>/_figures/figures.json`
and `files/`) and attaches each to its chapter by PDF page.

### Structural blind spots — NOT fixed by a model update

Two structural-fidelity gaps were found where Chandra **captures the content
but drops the block-type markup**:

- **Block quotes are not marked as block quotes** (page 12). The indented
  Ingersoll *Top Secret* quotation was rendered as ordinary paragraphs with
  quotation marks — no `>` / `<blockquote>` markup. Integrity risk: an unmarked
  block quote can later be misattributed as the author's own words rather than a
  quotation.
- **Complex horizontal / 2-D task-org tables are flattened** (page 155). The
  multi-column task-organization table was emitted as sequential vertical lists
  with **no `<table>` markup at all**. (Simple vertical tables — page 103
  casualties — are handled well, with correct `<table>` + `<u>` + totals.)

These were re-tested on the **updated** image (chandra-ocr 0.2.0, current
weights, pushed 2026-09-20) and **neither gap closed**. They are therefore
persistent Chandra limitations, not staleness — they must be addressed in the
pipeline (block-quote re-marking; table re-structuring or `needs_review`
flagging), not by waiting on a newer model. See INGESTION_FRONT_END.md
"Chandra markdown structural blind spots".

Pages handled well (no action needed): footnotes (page 20 — body + `<sup>`
markers + `---` divider + numbered citation list) and simple vertical tables
(page 103).

### Version pinning (reproducibility) — DONE

`Dockerfile.chandra` now pins everything: `chandra-ocr[hf]==0.2.0`, `transformers==5.17.0`,
`tokenizers==0.23.2`, `accelerate==1.15.0`, `huggingface_hub==1.32.0`, `torch==2.5.1+cu121` /
`torchvision==0.20.1+cu121` (force-reinstalled with `--no-deps` over the version `chandra-ocr`
pulls, plus the exact `nvidia-*` CUDA 12.1 runtime wheels), and the model
`datalab-to/chandra-ocr-2` at revision `af93b47dba1b47b6640c86ccf487ed2260ab9a09`
(`ENV CHANDRA_MODEL_REVISION`; the build asserts the downloaded commit matches). Bump them
deliberately.

### Render DPI: automatic OCR runs the 192 baseline; the manual script defaults to 300

Investigated 2026-09-22. **Current state (2026-11-16):** the DPI is overridable by the `IMAGE_DPI`
container environment variable (a pydantic setting; no image rebuild). `scripts/submit_ocr_job.py`
passes it from `--dpi` (default **300**). The automatic path (`trigger_handler._ocr_jobs`) sets no
`IMAGE_DPI`, so uploads OCR'd by the trigger run at Chandra's 192 default. The analysis below is
unchanged. Chandra's page-render DPI is a **library setting**
(`chandra/settings.py: IMAGE_DPI = 192`), **not** exposed on the CLI — so our
`chandra --method hf ... --page-range ...` invocation runs at the 192 baseline.
Chandra's own benchmark script (`scripts/olmocr_bench.py`) defaults to **300**
and explicitly labels 192 the "stock baseline". The gap is material:

| Render DPI | Rendered px | Pixels the model actually sees* | Megapixels |
|-----------:|------------:|--------------------------------:|-----------:|
| 192 (ours) | 1632×2112   | 1624×2100                       | 3.41 MP    |
| 300 (rec.) | 2550×3301   | 2184×2856                       | 6.24 MP    |
| 400        | 3400×4400   | 2184×2856                       | 6.24 MP    |

*After `chandra/model/util.py: scale_to_fit(max_size=(3072,2048))` downscales
every page image before inference. **Key finding:** 192→300 gives the model
**+83% usable detail**; the `scale_to_fit` ceiling caps gains **above** 300, so
300 is the sweet spot (400 is identical to 300, wasted render cost). Our current
192 leaves ~45% of usable input resolution unused — a corpus-wide config
decision worth revisiting, especially for dense/table-heavy pages. Raising it
requires overriding the library setting (env/config or `load_pdf_images(...,
image_dpi=300)`), since the CLI has no DPI flag. NOTE: extra resolution may help
marginal pages but is **not** expected to fully fix the 2-D task-org flattening
(a model reasoning limit, not a resolution limit); an empirical 192-vs-300
re-OCR of p155/p103 is the way to confirm (`tmp/dpi_probe.py`).

### Augmenting Chandra with a second OCR engine — PP-StructureV3 (implemented 2026-09-22)

For the persistent structural blind spots (2-D task-org tables, block quotes),
the 2025–2026 best-practice is a **pipeline** (VLM-OCR → structured parsing →
dedicated table extraction), i.e. **augment Chandra, don't replace it**. This is
now **built and deployed** with **PaddleOCR PP-StructureV3** as the table
specialist; the other engines below remain unused cross-check options.

- **PaddleOCR PP-StructureV3** (Apache-2.0) — **IMPLEMENTED.** Dedicated
  table-structure recognition (rows/cols/cells→HTML) + multi-column
  reading-order recovery. Directly targets the p155 2-D-table flattening. A PoC
  (2026-09-22) confirmed the **server** text models materially out-transcribe
  the mobile ones on this content, and the validated configuration is now an
  in-repo runner (`src/ingestion/paddle_structure.py`) driven by an AWS Batch
  GPU worker (`Dockerfile.paddle`, `scripts/paddle_recover_page.py`). See
  [../OCR_OPERATIONS.md](../OCR_OPERATIONS.md) "Table Recovery (PP-StructureV3)"
  for operations.
- **Docling** (IBM, Apache-2.0) — layout-aware PDF→Markdown/JSON; still a viable
  cross-check / second opinion (not currently wired).
- **Marker** (Datalab — *same team as Chandra*) — fast, but GPL-3.0 + RAIL-M
  weight license restricts commercial use above a revenue threshold, and likely
  shares Chandra's blind spots (weak augmentation choice).
- Broader self-hostable VLM-OCR field: PaddleOCR-VL, DeepSeek-OCR, dots.ocr,
  GOT-OCR 2.0, Granite-Docling.

**Implemented shape:** Chandra stays the default; table-heavy / suspect pages
are **pre-emptively routed** through PP-StructureV3 (the routing decision runs
locally by scanning Chandra's markdown for the flattened signature via
`table_recovery.page_needs_recovery`, so GPU cost is spent only on pages that
flattened). The two engines' outputs are kept side-by-side — where they
**disagree** on structure the region is flagged `needs_review`
(ensemble-as-verification — the same "make corruption loud" stance as the
loader's skip-logging and `scripts/utils/check_output_integrity.py`); recovered tables
are spliced back non-destructively via `table_merge_back.py`. Block quotes are
handled by post-processing regardless of engine
(`markdown_structure.py`, implemented).

### Recovery limits & the human-review compromise (decided 2026-09-24)

PP-StructureV3 recovery is **not** guaranteed to reconstruct every flattened
table, and we deliberately do not chase 100%. Validated on the St. Vith Appendix
"A" TROOP ASSIGNMENTS spread:

- **p155** (dense, column-aligned grid) → PP-StructureV3 reconstructs a real 2-D
  `<table>` (timecodes × commands). ✅
- **p156** (sparse, scattered — TF JONES/LINDSEY + "Atchd 7th Armd Div" blocks
  at irregular positions, margin notes) → recovers **0 tables**. Diagnosed via
  layout-box instrumentation (`RecoveredTable.layout_boxes`): the layout
  detector emits only `text` (0.49, 0.43) + `paragraph_title` (0.32) boxes —
  **no `table`-class box at any score**. This is a layout *misclassification*,
  one stage upstream of table recognition, so it is **not** fixable by
  `layout_threshold` (nothing to admit) or by orientation tuning.

**Decision: such layout-miss edge cases are resolved by human review, not by
further recovery-engine work.** This is the same "verification/flagging, not
fabrication" compromise the pipeline already makes elsewhere (OOB
`(unknown)`-division rows, fuzzy name-match confirmations, block-quote flags).
Concretely:

- The common case (dense tables, and the OOB corpus generally) is recovered
  automatically to `<table>`.
- The edge case is **flagged, never dropped**: the page's Chandra-parsed
  structure is preserved in the recovery JSON's `flattened_hints`
  (group→units, `needs_review`) for a human to complete into a grid. **Note:**
  `flattened_hints` preserve the *content and grouping*, not a reconstructed 2-D
  grid — a reviewer supplies the column crosstab.
- We spend GPU/engineering only where it pays off; forcing a table engine on
  genuinely borderless, sparse pages fights the data.

**Not pursued (available if a specific page proves high-value):** (A) preprocess
the image (tight crop / synthesize faint rules) so the layout model fires the
`table` class; (B) run the table-structure model directly on a cropped region,
bypassing layout detection. **Open follow-up (deferred):** ensure flagged pages'
`flattened_hints` actually reach a reviewer with the page image + parsed groups
through the existing review surface — see TODO.

---

## Architecture

*(Rewritten 2026-11-16 to match the code; the first design had a plain "Lambda: merge chunks" step.)*

```
upload to contentrepository/ (PDF or scan image)
    │  trigger Lambda: AV gate -> _ocr_chunks (page count) -> write ocr-output/<slug>/.chunks.json
    │  (BEFORE submitting) -> NAT up -> submit one Batch job per chunk
    ▼
AWS Batch queue {env}-wwii-chandra-gpu (SPOT_CAPACITY_OPTIMIZED; g5/g6 xlarge/2xlarge, ≥24 GB VRAM)
    │  hourly lambda_handlers/ocr_spot_controller: a job RUNNABLE too long on spot is moved to
    │  {env}-wwii-chandra-gpu-ondemand (48 h cumulative on-demand cap)
    ▼
Docker wwii-chandra (scripts/chandra_entrypoint.sh): chandra --method hf --paginate_output
    under scripts/ocr_watchdog.py  ->  s3://.../ocr-output/<slug>/chunk-pNNNN-NNNN/input/{input.md,
    input_metadata.json,*.webp}   (whole-PDF job: ocr-output/<slug>/input/input.md)
    │
    ▼  EventBridge: Batch job SUCCEEDED (every chandra queue) -> lambda_handlers/ocr_merge_handler
Merge Lambda: waits until EVERY chunk in .chunks.json has fresh markdown, claims the merge
    (create-only .merged), writes ocr-output/<slug>/merged/raw.md, then
    ├─ structured reference doc (.structured marker)  -> OOB parser task (phase0_ingest)
    ├─ upload has a validation URL -> phase0_ocr_crosscheck ECS task (cross-checks the OCR against
    │   the URL's HTML, then calls ocr_promote.promote)
    └─ otherwise ocr_promote.promote directly: sanitise -> translate per page -> split chapters
        -> normalise (chandra_markdown) -> contentrepository/<slug>/chapterN/chapterN-{meta.yaml,content.md}
    ▼
S3 event on chapterN-content.md starts Phase 1
```

---

## Parallelization Strategy

Chandra accepts `--page-range` to process a subset of pages. `trigger_handler._ocr_chunks` splits a PDF
longer than `OCR_CHUNK_PAGES` (default **50**) into 50-page ranges (`1-50`, `51-100`, ...); a PDF of
50 pages or fewer, an unreadable PDF and a single-page scan image run as one whole-input job. Each
chunk is an independent Batch job writing `ocr-output/<slug>/chunk-pNNNN-NNNN/` (names derived from
the 1-based page range, so re-running one range overwrites only its own output).

*(2026-11-16: the design's sizing table (4-20 instances) and the `submit_chandra_jobs` / `merge_chunks`
sketches were removed. The real submitters are `trigger_handler._submit_ocr` /
`_ocr_jobs` and `scripts/submit_ocr_job.py` (manual: whole PDF, `--page-range`, or `--manifest`).)*

### Merge step

Every chunk's SUCCEEDED event invokes `ocr_merge_handler`. Without a list of the expected chunks the
merge used to promote a partial book per finished chunk (re-triggering parse and extraction), so the
trigger writes `.chunks.json` before submitting (`src/ingestion/ocr_chunk_manifest.py`: submission id,
chunk list, `source_key`, `validation_url`). The merge proceeds only when every listed chunk's
markdown exists and is newer than the manifest, and the first merge to create `.merged`
(`IfNoneMatch='*'`) wins; a claim abandoned by a dead merge is taken over after
`OCR_MERGE_CLAIM_TTL` (default 3 h). Merged text goes to `merged/raw.md`; what happens next is in the
Architecture diagram. A stopped `phase0-crosscheck` task is relaunched (with more memory) by
`ocr_merge_handler.handle_crosscheck_stopped`.

---

## AWS Batch Configuration

Defined in `cloudformation/ocr.yaml` (the YAML in the first design was removed; read the template).

- **Compute environment** `{env}-wwii-chandra-gpu-spot-v4` (type is a parameter, default EC2; spot
  uses `SPOT_CAPACITY_OPTIMIZED`): instance types `g5.xlarge/2xlarge`, `g6.xlarge/2xlarge` only.
  `g4dn` (16 GB) is excluded because Chandra needs ~9-10 GB of GPU memory per page and OOMs there.
  `MinvCpus 0`, `MaxvCpus` 280 (parameter), AMI type `ECS_AL2023_NVIDIA`, private subnets.
- **Queue** `{env}-wwii-chandra-gpu`; **fallback** CE/queue `{env}-wwii-chandra-gpu-ondemand`
  (`OnDemandMaxvCpus` 96) used by `ocr_spot_controller`.
- **Job definition** `{env}-wwii-chandra`: 4 vCPU, 15000 MiB, 1 GPU, 2 attempts,
  `OCR_NO_PROGRESS_SECS=900` (the progress watchdog is the real stall detector),
  `AttemptDurationSeconds` 14400 (loose cost backstop only).
- A Paddle (PP-StructureV3) job definition and table-recovery worker share this GPU queue.

---

## Docker Image

`Dockerfile.chandra` (CUDA 12.4.1 runtime, Ubuntu 22.04). All versions and the model revision are
pinned (see "Version pinning"); model weights are baked in, so jobs do not download ~8 GB. The
entrypoint is `scripts/chandra_entrypoint.sh`: validate page ranges, download the input,
run `chandra --method hf --paginate_output` under `scripts/ocr_watchdog.py` (fails the job on lack of
progress or a CUDA OOM, and never uploads partial output), then `aws s3 sync` to the output prefix.
`--paginate_output` writes per-page separators that `src/ingestion/chunk_pages.py` uses to map a
flattened table back to its physical page.

---

## Cost Estimate

| Component | Cost |
|-----------|------|
| g5.xlarge Spot (per hour) | ~$0.35 |
| Typical job duration (50 pages) | ~3 min |
| Cost per 50-page chunk | ~$0.02 |
| 200-page PDF (4 parallel jobs) | ~$0.08 |
| 500-page PDF (10 parallel jobs) | ~$0.18 |
| ECR image storage (15GB with model) | ~$1.50/month |
| S3 output storage | Negligible |

**Min vCPUs = 0:** Instances scale to zero when idle. No cost when not processing.

---

## Integration with Existing Pipeline

Chandra is the PDF/scan -> markdown step of Phase 0 (`lambda_handlers/trigger_handler.py` routes the
upload; see `INGESTION_PATHS.md`). Its markdown (HTML tables included) is cleaned by
`src/ingestion/ocr_promote.promote`, which also translates non-English pages
(`LANGUAGE_TRANSLATION.md`) and, when the upload carries a validation URL, corrects the OCR against it
(`phase0_ocr_crosscheck.py`). Structured reference books (ETO Order of Battle) are routed to the OOB
parser track instead of the chapter path (`INGESTION_FRONT_END.md`, `STRUCTURED_DATA_ROUTING.md`).
Output chapters land in `contentrepository/<slug>/chapterN/`, and the content upload starts Phase 1.

---

## Chandra CLI Reference

```
chandra [OPTIONS] INPUT_PATH OUTPUT_PATH

Options:
  --method [hf|vllm]           hf = local model, vllm = remote server
  --page-range TEXT            e.g., "1-50,55,60-100"
  --max-output-tokens INT      Max tokens per page (default 12384)
  --max-workers INT            Parallel workers (vllm mode only)
  --include-images / --no-images
  --include-headers-footers / --no-headers-footers
  --batch-size INT             Pages per batch (hf mode, default 1)
  --paginate_output            Add page markers in output
```

---

## Model Details

| Property | Value |
|----------|-------|
| Model | `datalab-to/chandra-ocr-2` |
| Type | Vision-language model |
| Size | ~8B parameters |
| VRAM required | ~16GB (fits on A10G 24GB) |
| Input | PDF page rendered as image (192 DPI) |
| Output | Markdown with HTML tables |
| Strengths | Tables, forms, structured documents |

---

## Open Questions (status 2026-11-16)

1. **Model download in image vs. runtime** — decided: baked into the image.
2. **vLLM server mode** — not adopted; jobs use `--method hf`.
3. **Table post-processing** — HTML tables are handled by Phase 0 OOB parsers and the table-recovery
   worker (`src/ingestion/table_recovery.py`, `chunk_pages.py`); the parser handles HTML tables in prose.
4. **Accuracy validation** — addressed per book by the PDF <-> HTML cross-check when a validation
   URL exists (`src/ingestion/ocr_crosscheck.py`).
5. **NAT requirement** — decided: the trigger requests the dynamic NAT before submitting OCR
   (`_ensure_nat_for_ocr`), and `nat_manager` keeps it up while OCR jobs are in flight.

---

## Related

- [core/PIPELINE.md](../core/PIPELINE.md) — Phase 1 parsing (consumes markdown)
- [OCR_OPERATIONS.md](../OCR_OPERATIONS.md) — operating the OCR pipeline
- [pipeline/PDF_CONVERSION.md](../pipeline/PDF_CONVERSION.md) — Current PDF conversion approach
- [NETWORKING_LIFECYCLE.md](../NETWORKING_LIFECYCLE.md) — NAT for ECR pulls
