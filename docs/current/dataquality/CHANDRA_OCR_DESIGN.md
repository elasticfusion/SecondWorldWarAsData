# Chandra OCR: GPU-Accelerated PDF Processing

Design document for deploying Chandra OCR on AWS with GPU instances for high-quality PDF-to-markdown conversion.

**Status:** Deployed (AWS Batch GPU, `dev-wwii-chandra` job def / `dev-wwii-chandra-gpu` queue) | **Last Updated:** 2026-09-20

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
The `entrypoint.sh` forwards the arg verbatim, so this offset is inherent to
`chandra` itself, not the wrapper. Any manifest or submission helper must apply
the `N-1` conversion (and the automatic full-document chunking is unaffected
only because it ranges the whole doc).

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

### Version pinning (reproducibility)

`Dockerfile.chandra` installs `chandra-ocr[hf]`, `torch`/`torchvision`, and the
`datalab-to/chandra-ocr-2` weights **unpinned**, so a `--no-cache` rebuild
resolves whatever is current at build time. The 2026-09-20 rebuild resolved:
`chandra-ocr 0.2.0`, `transformers 5.17.0`, `torch 2.5.1+cu121`,
`torchvision 0.20.1+cu121` (note the Dockerfile force-reinstalls the cu121
torch build over the version `chandra-ocr` pulls). For a citable dataset,
"which Chandra produced this output" should be answerable — pin these versions
(and a specific HF model revision), or at minimum record the resolved versions
per build.

### Render DPI: we run the 192 baseline, not the recommended 300

Investigated 2026-09-22. Chandra's page-render DPI is a **library setting**
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
loader's skip-logging and `scripts/check_output_integrity.py`); recovered tables
are spliced back non-destructively via `table_merge_back.py`. Block quotes are
handled by post-processing regardless of engine
(`markdown_structure.py`, implemented).

---

## Architecture

```
S3 (source PDFs)
    │
    ▼
AWS Batch Job Queue (GPU)
    │ submits N parallel jobs, one per page range
    ▼
┌──────────────────────────────────┐
│  EC2 Spot g5.xlarge (A10G 24GB) │  ← one per job
│  Docker: chandra-ocr image      │
│  chandra --method hf            │
│    --page-range "51-100"        │
│    input.pdf output/            │
└──────────────────────────────────┘
    │
    ▼
S3 (markdown output per chunk)
    │
    ▼
Lambda: merge chunks → final markdown
    │
    ▼
S3 (contentrepository/{Book}/*.md)
    → existing pipeline ingests as normal
```

---

## Parallelization Strategy

Chandra accepts `--page-range` to process a subset of pages. Split large PDFs into chunks and process concurrently:

| PDF Size | Pages | Chunks | Pages/Chunk | Instances | Wall Time |
|----------|-------|--------|-------------|-----------|-----------|
| Small | <50 | 1 | All | 1 | ~2 min |
| Medium | 50-200 | 4 | 50 | 4 | ~3 min |
| Large | 200-500 | 10 | 50 | 10 | ~3 min |
| Very large | 500+ | 10-20 | 50 | 10-20 | ~5 min |

Each chunk is independent — no coordination needed between jobs except the final merge.

### Job Submission Example

```python
import boto3
import math

def submit_chandra_jobs(pdf_s3_key: str, total_pages: int, chunk_size: int = 50):
    batch = boto3.client("batch")
    job_ids = []
    chunks = math.ceil(total_pages / chunk_size)

    for i in range(chunks):
        start = i * chunk_size + 1
        end = min((i + 1) * chunk_size, total_pages)

        resp = batch.submit_job(
            jobName=f"chandra-{pdf_s3_key.split('/')[-1]}-p{start}-{end}",
            jobQueue="dev-wwii-chandra-gpu",
            jobDefinition="dev-wwii-chandra",
            containerOverrides={
                "command": [
                    "--method", "hf",
                    "--page-range", f"{start}-{end}",
                    f"s3://dev-wwii-data-pipeline/source/{pdf_s3_key}",
                    f"s3://dev-wwii-data-pipeline/ocr-output/{pdf_s3_key}/chunk-{i:03d}/",
                ],
            },
        )
        job_ids.append(resp["jobId"])

    return job_ids
```

### Merge Step

After all chunks complete, concatenate outputs in page order:

```python
def merge_chunks(pdf_s3_key: str, num_chunks: int, output_key: str):
    s3 = boto3.client("s3")
    merged = []
    for i in range(num_chunks):
        prefix = f"ocr-output/{pdf_s3_key}/chunk-{i:03d}/"
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
        for obj in sorted(resp.get("Contents", []), key=lambda x: x["Key"]):
            body = s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
            merged.append(body.decode("utf-8"))

    s3.put_object(
        Bucket=BUCKET,
        Key=output_key,
        Body="\n\n".join(merged).encode("utf-8"),
    )
```

---

## AWS Batch Configuration

### Compute Environment

```yaml
ChandraComputeEnv:
  Type: AWS::Batch::ComputeEnvironment
  Properties:
    Type: MANAGED
    State: ENABLED
    ComputeResources:
      Type: SPOT
      BidPercentage: 70
      MaxvCpus: 32
      MinvCpus: 0
      DesiredvCpus: 0
      InstanceTypes:
        - g5.xlarge    # 1× A10G, 24GB VRAM, 4 vCPU, 16GB RAM
        - g5.2xlarge   # 1× A10G, 24GB VRAM, 8 vCPU, 32GB RAM (fallback)
      Subnets:
        - !Ref PrivateSubnet1Id
        - !Ref PrivateSubnet2Id
      SecurityGroupIds:
        - !Ref LambdaSGId
      Ec2Configuration:
        - ImageType: ECS_AL2_NVIDIA
```

### Job Definition

```yaml
ChandraJobDef:
  Type: AWS::Batch::JobDefinition
  Properties:
    JobDefinitionName: dev-wwii-chandra
    Type: container
    ContainerProperties:
      Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/wwii-chandra:latest"
      ResourceRequirements:
        - Type: VCPU
          Value: "4"
        - Type: MEMORY
          Value: "15000"
        - Type: GPU
          Value: "1"
      ExecutionRoleArn: !Ref ECSTaskExecutionRoleArn
      JobRoleArn: !Ref ECSTaskRoleArn
      LogConfiguration:
        LogDriver: awslogs
        Options:
          awslogs-group: !Ref ChandraLogGroup
          awslogs-region: !Ref AWS::Region
    RetryStrategy:
      Attempts: 2
    Timeout:
      AttemptDurationSeconds: 900  # 15 min max per chunk
```

### Job Queue

```yaml
ChandraJobQueue:
  Type: AWS::Batch::JobQueue
  Properties:
    JobQueueName: dev-wwii-chandra-gpu
    State: ENABLED
    Priority: 1
    ComputeEnvironmentOrder:
      - ComputeEnvironment: !Ref ChandraComputeEnv
        Order: 1
```

---

## Docker Image

```dockerfile
FROM nvidia/cuda:12.4-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip && \
    pip3 install "chandra-ocr[hf]" boto3 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Pre-download model weights (avoids 8GB download per job)
RUN python3 -c "from transformers import AutoModel, AutoProcessor; \
    AutoProcessor.from_pretrained('datalab-to/chandra-ocr-2'); \
    AutoModel.from_pretrained('datalab-to/chandra-ocr-2')"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

### Entrypoint

```bash
#!/bin/bash
# entrypoint.sh — download PDF from S3, run chandra, upload results

INPUT_S3="$1"
shift
OUTPUT_S3="$1"
shift

LOCAL_INPUT="/tmp/input.pdf"
LOCAL_OUTPUT="/tmp/output"

# Download
aws s3 cp "$INPUT_S3" "$LOCAL_INPUT"

# Run chandra
chandra --method hf "$@" "$LOCAL_INPUT" "$LOCAL_OUTPUT"

# Upload results
aws s3 sync "$LOCAL_OUTPUT" "$OUTPUT_S3"
```

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

### Trigger Options

1. **Manual** — Lambda or CLI invocation for ad-hoc PDFs
2. **S3 event** — New PDF uploaded to `source/` prefix triggers job submission
3. **Pipeline Phase 0** — Before Phase 1 parsing, convert any PDFs to markdown.
   The Phase 0 ingestion front-end (`src/ingestion/`) consumes Chandra markdown:
   it classifies each page's disposition and, for scanned reference tables,
   parses the markdown into structured rows (see
   [INGESTION_FRONT_END.md](INGESTION_FRONT_END.md) and
   [STRUCTURED_DATA_ROUTING.md](STRUCTURED_DATA_ROUTING.md)). Chandra is the
   PDF→markdown OCR bridge feeding that front-end.

### Output Format

Chandra produces markdown with HTML tables. This is directly compatible with the existing Phase 1 parser (for prose) and with the Phase 0 OOB section parsers (for scanned tables) — no conversion needed. Output goes to `contentrepository/{Book}/` alongside other markdown chapter files.

### Workflow

```
Upload PDF to S3 → Lambda detects new PDF
  → Count pages (PyPDF2)
  → Submit N Batch jobs (page-range chunks)
  → EventBridge monitors job completion
  → All chunks done → merge Lambda fires
  → Final markdown written to contentrepository/
  → S3 notification triggers Phase 1 (existing flow)
```

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

## Open Questions

1. **Model download in image vs. runtime** — Baking the model into the Docker image adds ~8GB to ECR storage (~$1.50/month) but eliminates download time per job. Recommended: bake it in.

2. **vLLM server mode** — For high-volume processing, a persistent g5 instance running vLLM would be more efficient than per-job startups. Evaluate if processing >10 PDFs/week.

3. **Table post-processing** — Chandra outputs HTML tables. The existing pipeline expects markdown. May need a conversion step, or update Phase 1 to handle HTML tables directly.

4. **Accuracy validation** — Compare Chandra output against existing manually-converted markdown for a known chapter. Quantify error rate before adopting for all PDFs.

5. **NAT requirement** — Batch GPU instances need internet for ECR pull. Reuse existing dynamic NAT, or place in public subnet with EIP.

---

## Related

- [core/PIPELINE.md](../core/PIPELINE.md) — Phase 1 parsing (consumes markdown)
- [pipeline/PDF_CONVERSION.md](../pipeline/PDF_CONVERSION.md) — Current PDF conversion approach
- [NETWORKING_LIFECYCLE.md](../NETWORKING_LIFECYCLE.md) — NAT for ECR pulls
