# Unattended End-to-End Readiness — Backlog

**Goal:** run the full pipeline **unattended** on the WWIIArchives backlog (628
files) — raw upload → OCR → parse → extract → dedup → enrich → done, with no
manual intervention, failures that self-signal, and bounded cost.

**Status (2026-09-28):** NOT yet unattended. Concurrent OCR *dispatch* works
(verified: 5 PDFs → 5 parallel GPU OCR jobs via S3-upload triggers). But the
OCR→parse handoff is broken (see #1), so raw PDFs dead-end after OCR. This doc
tracks the gaps found while testing, in priority order.

---

## 1. OCR → parse handoff is UNWIRED (CRITICAL — the pipeline dead-ends after OCR)

**Symptom (verified 2026-09-28):** 5 B-series PDFs OCR'd successfully → each wrote
`ocr-output/{book}/input/input.md` — but **0 reached `contentrepository/`**, so the
parse phase never triggered. The pipeline stops dead after OCR.

**Root cause:** OCR output lands in `ocr-output/`, but:
- The S3 notification only fires on `contentrepository/` and `output/content/*-parsed.json` — **nothing watches `ocr-output/`**.
- The merge/promote step (`submit_ocr_job.py::merge_outputs`, which writes
  `contentrepository/{book}/{book}.md`) only runs in the **manual `--wait` CLI path**,
  NOT the event-driven trigger path.

**Fix:** on Chandra OCR-job SUCCEEDED, run the merge (`ocr-output/{book}/` →
`contentrepository/{book}/{book}.md`), which then triggers parse. Options:
- **EventBridge Batch job-state-change rule** (job SUCCEEDED, queue=chandra) → a
  small **merge Lambda** that promotes OCR output to `contentrepository/`.
- Or the OCR container writes the merged markdown to `contentrepository/` at the end.

Preferred: EventBridge → merge Lambda (keeps the merge logic testable, reuses
`merge_outputs`).

## 2. GPU-OCR should run SPOT with a forgiving instance pool + hourly-retry / 48h on-demand cap

**Current:** OCR compute env is `EC2` (on-demand, ~$5/hr for 5 GPUs). `ocr.yaml`
has a `ComputeType` param (EC2/SPOT) — deployed as EC2.

**Target (operator spec):**
- Prefer **SPOT**; poll **hourly** for spot GPU capacity.
- **Forgiving instance selection:** any GPU meeting the OCR spec (1 GPU, ~15GB mem)
  — widen beyond g5/g6 to g4dn etc. with `SPOT_CAPACITY_OPTIMIZED` for a large pool.
- Fall back to **ON-DEMAND** only after spot unavailable, capped at **48h**.
- Non-OCR (parse/extract/enrich) = **simple CPU** (Fargate-Spot already 4:1).

**Note (§7.1):** the trigger's `_submit_ocr` submits the WHOLE PDF (no page-range
chunking), so a spot reclaim mid-OCR restarts the whole doc. Doing SPOT right also
needs **page-range chunking** so a reclaim loses one chunk, not the whole PDF.
Requires a controller (hourly spot check + on-demand routing + 48h cap) — Batch
has no native "hourly retry then fall back" (native ordered dual-env is the
simpler alternative; operator chose the custom controller for max spot savings).

## 3. Slack notification gap (pipeline events reach email, not Slack)

**Symptom (verified):** "Pipeline task launched: extract" arrived by **email but
not Slack**. `_notify_launch`/`_notify_complete` publish **free-form SNS text** to
the phase2-complete topic; AWS Chatbot **silently drops** non-alarm text (only
CloudWatch alarms render — proven by a manual alarm→Slack test that DID arrive).

**Fix:** a **formatter Lambda** subscribed to the notification topic that reposts
to Slack in Chatbot's supported custom-notification schema (or a direct Slack
webhook). Alarm-based alerts (credit-hold, spend-alert) already render — this is
only for free-form pipeline lifecycle notifications.

## 4. Double bibliography download (~34 min wasted per doc)

**Symptom:** Phase 2 unconditionally downloads the entire 13,566-file bibliography
(~17 min), and the submit-only delegation re-downloads it AGAIN (~17 min) — for
ANY extraction, even a single small doc. Also lets the NAT lease expire mid-download.

**Fix:** download the bibliography once (cache/share between the parent run_phase
and the submit-only subprocess), or scope the download to what the extraction
actually needs.

## 5. Silent OCR-timeout failure

**Current:** OCR jobs have `attemptDurationSeconds: 14400` (4h) + 2 retries + a
15-min no-progress watchdog (`ocr_watchdog.py`). A genuinely hung job dies in
~15 min (good), but after 2 failed attempts the job **fails terminally with no
notification** and the doc silently stalls (no OCR output → no parse).

**Fix:** on OCR-job FAILED (EventBridge), notify + route the doc to a needs-review
queue so an unattended run signals the failure instead of losing the doc.

## 6. M7 failure-cause taxonomy not wired into retry paths

Built + tested (`src/utils/failure_taxonomy.py`) but not invoked by the live
batch/retry code. Wire `classify()` into the batch retry + the SFN Catch so
failures route by cause (transient→retry, funding→hold, model→fallback,
permanent→needs-review) instead of ad-hoc handling.

## 7. Pre-stage / dispatcher (doc# seeding) for true multi-doc concurrency

The SFN Map dispatcher (M4.3) is deployed but dormant (`MULTI_DOC_ENABLED=false`)
and its `enumerate_pending` reads `doc#` lifecycle records that nothing creates
(M1 pre-stage unwired). For dispatcher-driven concurrency at scale, wire the
pre-stage to populate `doc#` records on upload, then enable multi_doc at pool>1.
(Path A — event-driven concurrent OCR — works today without the dispatcher, so
this is for the larger 628-file drain, not a blocker for basic parallelism.)

---

## Recommended sequence

1. **#1 OCR→parse handoff** — without it, no raw PDF completes; everything else
   is moot for the PDF path.
2. **#3 Slack notifications** — so unattended runs actually signal completion/failure.
3. **#2 SPOT + chunking + controller** — cost + resilience for the real drain.
4. **#5 failure-signal + #6 M7 wiring** — self-handling failures.
5. **#4 double-download** — efficiency.
6. **#7 dispatcher** — concurrency at full scale.

**Verified-working today:** concurrent OCR dispatch (5 parallel), URL-decode fix,
per-doc NAT leases (no churn), credit gate wiring, S3-upload triggers.
