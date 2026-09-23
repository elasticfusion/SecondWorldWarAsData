# OCR Standalone Operations

Deploy and run Chandra OCR independently from the main pipeline.

**Last Updated:** 2026-06-30

---

## Deploy

```bash
bash scripts/deploy_all.sh --ocr-standalone
```

This builds the Chandra Docker image (with GPU support and model weights baked in), pushes it to ECR, and deploys the AWS Batch infrastructure (GPU Spot instances, job queue, job definition). Does not touch the main pipeline stack.

**Prerequisites:** The network stack must already be deployed (provides subnets and security groups).

---

## Steps: First-Time Setup to Execution

### One-Time Setup

1. **Request GPU Spot quota** — G-type instances may default to 0 vCPUs for Spot. Request an increase (32 vCPUs = 8 parallel jobs):
   ```bash
   aws service-quotas request-service-quota-increase \
     --service-code ec2 --quota-code L-3819A6DF \
     --desired-value 32 --region us-east-1
   ```
   Wait for approval (typically a few hours).

2. **Ensure network stack exists** — The main pipeline stack (`wwii-pipeline-dev`) must be deployed, or at minimum the network stack (provides subnets, security groups, NAT manager Lambda).

3. **Deploy OCR stack** — Builds the Docker image, pushes to ECR, creates Batch resources:
   ```bash
   bash scripts/deploy_all.sh --ocr-standalone
   ```

### Per-PDF Workflow

4. **Upload PDF to S3:**
   ```bash
   aws s3 cp MyBook.pdf s3://dev-wwii-data-pipeline/source/MyBook.pdf --region us-east-1
   ```

5. **Create manifest** (optional — skip for auto-chunking):
   ```bash
   # manifests/mybook.txt
   1-20 #introduction
   21-50 #chapter_1
   51-80 #chapter_2
   ```

6. **Submit jobs:**
   ```bash
   python3 scripts/submit_ocr_job.py \
     s3://dev-wwii-data-pipeline/source/MyBook.pdf \
     --manifest manifests/mybook.txt \
     --wait --no-merge
   ```

---

## Submit Jobs

```bash
python3 scripts/submit_ocr_job.py <s3-path-to-pdf> [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--chunk-size N` | 50 | Pages per parallel job. Smaller = more parallelism, faster wall time |
| `--page-range "X-Y"` | All pages | Process specific pages only (submits a single job) |
| `--manifest FILE` | None | Submit one job per line from a manifest file (see below) |
| `--wait` | Off | Block until all jobs complete, then merge/publish outputs |
| `--no-merge` | Off | Don't concatenate. Publish each chunk as a separate file named by its label |
| `--output-key KEY` | `contentrepository/{name}/{name}.md` | S3 key for merged output |
| `--output-prefix PREFIX` | `contentrepository/{name}/` | S3 prefix for individual files (with `--no-merge`) |
| `--skip-networking` | Off | Skip NAT/VPC endpoint creation (use if networking is already up) |

### Examples

Process an entire PDF (auto-chunk, merge into one file):
```bash
python3 scripts/submit_ocr_job.py \
  s3://dev-wwii-data-pipeline/source/ETO_Order_of_Battle.pdf \
  --wait
```

Process specific pages only:
```bash
python3 scripts/submit_ocr_job.py \
  s3://dev-wwii-data-pipeline/source/ETO_Order_of_Battle.pdf \
  --page-range "21-34" \
  --wait
```

Smaller chunks for faster completion:
```bash
python3 scripts/submit_ocr_job.py \
  s3://dev-wwii-data-pipeline/source/LargeBook.pdf \
  --chunk-size 25 \
  --wait
```

Submit from a manifest (one job per section, individual output files):
```bash
python3 scripts/submit_ocr_job.py \
  s3://dev-wwii-data-pipeline/source/ETO_Order_of_Battle.pdf \
  --manifest manifests/eto_order_of_battle.txt \
  --wait --no-merge
```

Fire and forget (check later):
```bash
python3 scripts/submit_ocr_job.py \
  s3://dev-wwii-data-pipeline/source/Book.pdf
```

---

## Manifest Files

A manifest defines one job per line with a page range and a label. Use this when you know the document structure and want individual output files per section.

### Format

```
# Comments start with #
# Format: page-range #label
6-15 #preface
21-34 #1st infantry
35-45 #2nd infantry
46-57 #3rd infantry
```

Lines starting with `#` are skipped. The label after `#` becomes the output filename.

### Output Modes

**With `--no-merge`** (recommended for manifests):
```
s3://dev-wwii-data-pipeline/contentrepository/ETO_Order_of_Battle/preface.md
s3://dev-wwii-data-pipeline/contentrepository/ETO_Order_of_Battle/1st_infantry.md
s3://dev-wwii-data-pipeline/contentrepository/ETO_Order_of_Battle/2nd_infantry.md
...
```

Each section becomes its own file, named from the manifest label (spaces/special chars → underscores, lowercased).

**Without `--no-merge`** (default):
```
s3://dev-wwii-data-pipeline/contentrepository/ETO_Order_of_Battle/ETO_Order_of_Battle.md
```

All sections concatenated into one file in manifest order.

### Example Manifest

See `manifests/eto_order_of_battle.txt` — 68 sections covering every division in the ETO Order of Battle PDF.

### Concurrency

All manifest jobs submit at once. AWS Batch runs them in parallel up to the compute limit (`MaxvCpus: 32` = 8 simultaneous g5.xlarge). Remaining jobs queue in `RUNNABLE` state and run automatically as slots free up. No jobs are lost — Batch handles the queue.

---

## What Happens

### Without `--wait`

```
1. Invoke nat_manager → NAT + VPC endpoints created (for ECR image pull)
2. Count PDF pages (or read manifest)
3. Split into chunks / submit one job per manifest line
4. Submit jobs to AWS Batch queue
5. Exit immediately
```

Jobs run in the background on GPU Spot instances. Monitor with:
```bash
aws batch list-jobs --job-queue dev-wwii-chandra-gpu --job-status RUNNING --region us-east-1
```

You are responsible for merging/publishing outputs when done.

### With `--wait`

```
1. Invoke nat_manager → NAT + VPC endpoints created
2. Count PDF pages (or read manifest)
3. Submit all jobs
4. Poll every 30 seconds until all jobs complete
5. On success:
   - Default: merge all chunks → single markdown file
   - With --no-merge: publish each chunk as a named file from its label
6. Output written to contentrepository/{BookName}/
```

The output files are immediately available for the existing pipeline (Phase 1 parsing triggers on new `.md` files in `contentrepository/`).

---

## Infrastructure

| Component | Detail |
|-----------|--------|
| Compute | AWS Batch, GPU Spot (g5.xlarge — 1× A10G, 24GB VRAM) |
| Scaling | 0 instances when idle, up to 32 vCPUs under load |
| Cost | ~$0.35/hr per instance, ~$0.02 per 50-page chunk |
| Networking | Reuses existing NAT + VPC endpoints (nat_manager Lambda) |
| Logs | CloudWatch: `/aws/batch/dev-wwii-chandra` (14-day retention) |
| Retry | 2 attempts per job, 30-min timeout |

### Independence from Pipeline

- Separate CloudFormation stack (`wwii-ocr-dev`)
- Separate job queue and compute environment
- Shares only: subnets, security group, NAT manager
- No locks, no DynamoDB interaction
- Can run simultaneously with pipeline phases

---

## Monitoring

```bash
# List all jobs
aws batch list-jobs --job-queue dev-wwii-chandra-gpu --region us-east-1

# Describe a specific job
aws batch describe-jobs --jobs <job-id> --region us-east-1

# Tail logs
aws logs tail /aws/batch/dev-wwii-chandra --follow --region us-east-1

# Check compute environment (instances active)
aws batch describe-compute-environments \
  --compute-environments dev-wwii-chandra-gpu --region us-east-1 \
  --query 'computeEnvironments[0].computeResources.{desired:desiredvCpus,max:maxvCpus}'
```

---

## Troubleshooting

### Job stuck in RUNNABLE

Instances are provisioning (2-5 min for Spot GPU). If stuck >10 min:
- Check Spot capacity: Batch may not find available g5 instances in the region
- Verify networking: NAT must be up for ECR pull

```bash
# Force networking up
aws lambda invoke --function-name dev-wwii-nat-manager \
  --payload '{"action": "create"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
```

### Job FAILED

```bash
# Check reason
aws batch describe-jobs --jobs <job-id> --region us-east-1 \
  --query 'jobs[0].{status:status,reason:statusReason,log:container.logStreamName}'
```

Common causes:
- **OOM:** PDF page too large for 15GB memory limit. Try `--chunk-size 10`.
- **Spot reclaimed:** Automatic retry (2 attempts configured).
- **ECR pull failed:** Networking was torn down mid-pull. Re-submit.

### Merge didn't run

If you forgot `--wait`, merge manually:
```bash
# Re-run with --wait (will skip submission, just merge existing outputs)
python3 scripts/submit_ocr_job.py s3://dev-wwii-data-pipeline/source/Book.pdf --wait --skip-networking
```

---

## Output Format

Chandra produces markdown with HTML tables. Example:

```markdown
# 1st Infantry Division

## COMMAND AND STAFF

<table><tbody>
<tr><td rowspan="6">Comdg Gen</td><td>5 Nov 1943</td><td>Maj Gen Clarence R Huebner</td></tr>
<tr><td>7 Dec 1943</td><td>Brig Gen Willard G Wyman (Actg)</td></tr>
...
</tbody></table>

## STATISTICS

| Killed | 1,973 |
| Wounded | 11,448 |
| Days in Combat | 292 |
```

This format is directly compatible with Phase 1 parsing — no conversion needed.

---

## Table Recovery (PP-StructureV3)

Chandra flattens complex 2-D task-organization tables into vertical lists with
no `<table>` markup (a validated structural blind spot — see CHANDRA_OCR_DESIGN).
The OOB section parsers key off `<table>`, so those regions would otherwise be
dropped. A **second OCR engine, PaddleOCR PP-StructureV3**, recovers the real
grid from the page image. It runs as a **separate AWS Batch GPU job**
(`Dockerfile.paddle` → `{env}-wwii-paddle` job definition), independent of the
Chandra job.

**Cost posture — recovery only fires where Chandra failed.** The routing
decision runs *locally and cheaply*: `submit_table_recovery.py` scans each
page's Chandra markdown for the flattened-table signature
(`src/ingestion/table_recovery.page_needs_recovery`, reusing
`markdown_structure.detect_flattened_tables`). Only pages that actually
flattened are rendered, uploaded, and sent to a GPU job. Prose, images, and
already-correct `<table>` pages never reach Paddle.

### Deploy

The Paddle image is built and pushed by `deploy_all.sh` (same flow as Chandra);
model weights are downloaded at first job run, not baked into the image.

```bash
bash scripts/deploy_all.sh --ocr-standalone   # builds/pushes chandra + paddle images
```

### Submit

Run *after* Chandra has produced per-page markdown (`p<N>.md`, one file per
physical page) under an S3 prefix:

```bash
python3 scripts/submit_table_recovery.py \
  --pdf s3://dev-wwii-data-pipeline/source/ETO_Order_of_Battle.pdf \
  --markdown-prefix s3://dev-wwii-data-pipeline/ocr-output/ETO_Order_of_Battle/ \
  --wait
```

Per flattened page the submitter: (1) renders the PDF page to a 300-DPI PNG
(matching the OCR render standard), (2) uploads the image next to the existing
markdown, (3) submits a `{env}-wwii-paddle` Batch job via
`scripts/paddle_entrypoint.sh` → `scripts/paddle_recover_page.py`, which writes
a `p<N>.recovery.json`.

### Reconcile / merge back

Recovery is **ensemble-as-verification, not replacement.** The worker records
*both* Chandra's original (review-flagged) output and Paddle's recovered
`<table>` — it never silently overwrites one with the other. To splice recovered
tables back into the markdown in place of the flattened region (so the OOB
parsers see real `<table>` markup with no parser changes), use
`src/ingestion/table_merge_back.py`. The splice is non-destructive: the original
flattened lines are preserved in an adjacent HTML comment for audit, and each
spliced table is marked as PP-StructureV3-derived and review-flagged.

### Runner configuration (validated PoC, 2026-09-22)

`src/ingestion/paddle_structure.py` captures the validated config: **server**
text models (`PP-OCRv5_server_det`/`_rec`) for accuracy; `text_det_limit_side_len`
bounds detection input so the server det model doesn't over-allocate on a full
300-DPI render; oneDNN disabled (paddlepaddle 3.3.x MKL-DNN kernel bug on CPU;
harmless on GPU); GPU when available with CPU fallback; formula recognition off.
PaddleOCR is an **optional dependency** (only on the OCR worker) — the module
imports it lazily and exposes `is_available()` so the rest of the pipeline and
the tests degrade gracefully when it is absent.

### Security

The Paddle image is Trivy-scanned in the deploy path (blocking on HIGH/CRITICAL).
Security pins (`anyio`, `pillow`, `protobuf`) are applied *after* the paddle
stack in `Dockerfile.paddle` so they override its transitive versions; re-scan
after any paddle-stack bump.

---

## Related

- [dataquality/CHANDRA_OCR_DESIGN.md](dataquality/CHANDRA_OCR_DESIGN.md) — Full design document
- [NETWORKING_LIFECYCLE.md](NETWORKING_LIFECYCLE.md) — NAT/VPC endpoint management
- [pipeline/PDF_CONVERSION.md](pipeline/PDF_CONVERSION.md) — Previous PDF conversion approach
