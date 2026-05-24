# Data Flow Analysis: Reprocessing, Failure Points & Pipeline Visibility

**Date:** 2026-05-23  
**Issue:** Old data being reprocessed unexpectedly

---

## How Data Moves Through the Pipeline (AWS Mode)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ TRIGGER: S3 upload to content/*.md                                       │
│   → S3 notification → SNS (content-uploaded) → SQS → Trigger Lambda     │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE 1: ECS Task (phase1_parse.py)                                      │
│                                                                          │
│ 1. Clears ALL DynamoDB locks                                             │
│ 2. Resets dedup review status (complete: false)                          │
│ 3. Clears stale manifest                                                 │
│ 4. Downloads content from S3 (manifest keys OR full sync)                │
│ 5. Parses ALL downloaded markdown → JSON (NO skip logic)                 │
│ 6. Clears manifests/pending.json from S3                                 │
│ 7. Uploads ALL parsed files to output/content/ in S3                     │
│    ↓ S3 notification fires for EVERY -parsed.json uploaded               │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ TRIGGER: S3 notification on output/content/*-parsed.json                 │
│   → SNS (chapter-parsed) → SQS → Trigger Lambda                         │
│   → _queue_parsed(): head_object check — skips if -event.json exists     │
│   → _launch_phase2_if_idle()                                             │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE 2: ECS Task (phase2_extract.py)                                    │
│                                                                          │
│ 1. Downloads parsed files WITHOUT corresponding event files (incremental)│
│ 2. Extracts events → entities (parallel, max 3 chapters)                 │
│ 3. Retries failed event extractions                                      │
│ 4. Optional extractors (weather, equipment, etc.) — sequential           │
│ 5. Final sync: uploads entity files to S3                                │
│ 6. Writes manifest#phase2 to DynamoDB (list of uploaded keys)            │
│ 7. Runs dedup detection (downloads ALL entities cross-book)              │
│ 8. Checks pending#content queue → re-triggers Phase 1 if found          │
│ 9. Sends completion notification with dedup review URL                   │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ DEDUP GATE: Human reviews duplicates in web UI                           │
│   → Merge/reclassify/skip actions append keys to manifest#phase2         │
│   → Marks review_status.json complete: true                              │
│   → Publishes to SNS (dedup-complete) → Trigger Lambda                   │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ PHASE 3: ECS Task (phase3_enrich_data.py)                                │
│                                                                          │
│ 1. Stamps schema versions on all entity files                            │
│ 2. Downloads entities from manifest#phase2 (or full download if none)    │
│ 3. Enriches people, groups, places, bibliography                         │
│ 4. Skips entities with enrichment_status: "enriched" or "not_found"      │
│    (re-searches not_found after 90 days)                                 │
│ 5. Final sync: uploads ALL entity files (no skip_keys filtering)         │
│ 6. Sends completion notification                                         │
│ 7. Does NOT remove its own lock (prevents re-trigger from entity uploads)│
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Root Causes of Old Data Being Reprocessed

### Cause 1: Phase 1 Has No Skip Logic (CRITICAL)

**Phase 1 always re-parses everything it downloads.** There is no check for "does this parsed file already exist and is it unchanged?"

When Phase 1 runs with no manifest (fallback to full `content/` sync), it:
1. Downloads ALL markdown files from S3
2. Re-parses ALL of them
3. Uploads ALL parsed files to S3

Each uploaded `-parsed.json` fires an S3 notification → SNS → Trigger Lambda. The trigger Lambda's `_queue_parsed()` does a `head_object` check for the corresponding `-event.json`, which correctly filters out already-processed files. **But if the event file was deleted, corrupted, or the head_object check fails transiently, old content gets queued for Phase 2.**

**When does the full sync fallback happen?**
- When `manifests/pending.json` doesn't exist in S3 (cleared by Phase 1 itself at the end of each run)
- When the manifest is empty or has no `content/` prefixed keys
- When the `_check_pending_content()` re-trigger writes a manifest but the SNS publish fails and the next trigger has no manifest

### Cause 2: Phase 1 Clears ALL Locks on Start (HIGH)

Every Phase 1 run calls `_clear_all_locks()` which:
1. Deletes ALL `lock#*` entries from DynamoDB
2. Resets `dedup/review_status.json` to `{complete: false}`
3. Clears `manifest#phase2`

**Impact:** If Phase 1 is triggered while Phase 3 is running (e.g., new content uploaded), Phase 3's lock is cleared. When Phase 3 finishes and uploads entities, the trigger Lambda sees no lock and could potentially re-trigger processing.

More critically: clearing `manifest#phase2` means if Phase 3 hasn't started yet, it loses its incremental download list and falls back to downloading ALL entities.

### Cause 3: Phase 1 Re-trigger After Phase 2 (MEDIUM)

The `_check_pending_content()` mechanism:
1. Phase 2 completes → checks `pending#content` in DynamoDB
2. If keys exist → deletes the DynamoDB entry → writes `manifests/pending.json` → publishes to SNS
3. This triggers a new Phase 1 run

But the manifest written here contains only the NEW content keys. Phase 1 downloads only those keys (incremental). **This path is safe** — it only re-parses the new content.

However, if the manifest write succeeds but the SNS publish fails, the pending content is lost (DynamoDB entry already deleted). The next trigger will have no manifest → full sync → reprocesses everything.

### Cause 4: Hourly Stale Lock Check + Dedup Reconciliation (MEDIUM)

The hourly EventBridge rule:
1. Clears stale locks (correct behavior)
2. Checks if dedup is complete but Phase 3 never ran → triggers Phase 3

If `review_status.json` is stale from a previous run (Phase 1 didn't reset it due to a failure), the reconciliation logic could trigger Phase 3 for old data.

### Cause 5: Background Sync Re-uploads Unchanged Files (LOW)

`BackgroundSync` uploads entity files every 120 seconds without checking if they changed. This doesn't directly cause reprocessing (entity uploads don't trigger S3 notifications for the pipeline), but it does cause unnecessary S3 PUT operations.

### Cause 6: Phase 3 Final Sync Doesn't Use skip_keys (LOW)

```python
skip = _downloaded_keys if "phase2" in phase_script else set()
```

Phase 3 uploads ALL entity files (skip is empty set), including ones it downloaded but didn't modify. This doesn't trigger reprocessing (entity prefixes don't have S3 notifications), but wastes bandwidth.

---

## Failure Points That Could Cause Data Loss or Unintended Reprocessing

| # | Failure Point | Consequence | Likelihood |
|---|---------------|-------------|------------|
| 1 | Phase 1 manifest missing → full sync | ALL content re-parsed, ALL parsed files re-uploaded, S3 notifications fire for all | **High** — happens every time manifest is cleared |
| 2 | `_check_pending_content` deletes DynamoDB before SNS publish succeeds | Pending content lost permanently | Medium |
| 3 | Phase 1 clears locks while Phase 2/3 is running | Running phase loses its lock, potential concurrent execution | Medium |
| 4 | ECS task killed mid-run (OOM, spot termination) | Lock persists (stale), partial output in S3, next run may see inconsistent state | Medium |
| 5 | `_queue_parsed` head_object check fails (S3 eventual consistency, throttling) | Already-processed file queued for Phase 2 re-extraction | Low-Medium |
| 6 | Trigger Lambda SQS batch window (60s) groups events from multiple uploads | Single Phase 1 task processes multiple books, manifest contains mixed keys | Low |
| 7 | Phase 2 batch mode: submit succeeds but poller never retrieves | Job stuck as "pending" until 24h timeout, then treated as complete with partial results | Low |
| 8 | Dedup review_status.json not reset (Phase 1 failure) | Stale "complete" status triggers Phase 3 for old data via hourly reconciliation | Low |

---

## The Most Likely Scenario for Your Issue

Based on the code analysis, the most probable cause of old data being reprocessed:

1. **New content is uploaded** → triggers Phase 1
2. Phase 1 calls `_clear_all_locks()` and clears `manifest#phase2`
3. Phase 1 reads `manifests/pending.json` — **if it doesn't exist** (was cleared by a previous Phase 1 run), it falls back to `s3_sync_down("content/", ...)` which downloads ALL content
4. Phase 1 re-parses ALL books (not just the new one)
5. Phase 1 uploads ALL parsed files → S3 notifications fire for every file
6. Trigger Lambda's `_queue_parsed()` checks each file with `head_object` for event files
7. If any event file is missing (deleted during dedup, or from a book that was never fully processed), that old content gets queued for Phase 2

**The fix:** Phase 1 should either:
- Only parse files that are newer than their existing parsed output (mtime comparison)
- Or maintain a hash/checksum of source content and skip unchanged files
- Or scope the full-sync fallback to only the book(s) that triggered the run (use `BOOK_NAME` env var)

---

## Pipeline Visibility Recommendations

### Option 1: Enhance the Existing CloudWatch Dashboard (Quick Win)

The CloudFormation already deploys a basic dashboard (`events.yaml` → `PipelineDashboard`). It currently shows Lambda invocations, errors, and duration. Extend it with:

- **ECS Task status** — running/stopped/failed counts per task family
- **DynamoDB lock state** — custom metric from the trigger Lambda showing which locks are active
- **Batch job progress** — custom metric from the poller showing pending/complete/failed counts
- **S3 object counts** — per-prefix counts showing pipeline progress (parsed files vs event files)

**Effort:** 1-2 hours. Add custom CloudWatch metrics from the trigger Lambda and batch poller.

### Option 2: DynamoDB-Backed Status Page (Medium Effort)

You already have the dedup review UI (Lambda + API Gateway). Add a `/status` endpoint that reads:
- All `lock#*` entries → shows which phases are active
- All `batch_job#*` entries → shows batch progress
- `pending#content` and `pending#parsed` → shows queued work
- `manifest#phase2` → shows what Phase 3 will process
- ECS `list_tasks` → shows running containers

Render as a simple HTML page or JSON API. The dedup UI Lambda already has the IAM permissions.

**Effort:** 4-6 hours. Reuses existing infrastructure.

### Option 3: Step Functions Visualization (Best Long-Term)

Replace the current SNS → SQS → Lambda → ECS orchestration with AWS Step Functions. This gives you:
- Visual execution graph in the AWS console
- Built-in retry/catch/timeout handling
- Execution history with input/output for each step
- No custom lock management needed (Step Functions handles concurrency)
- CloudWatch Metrics integration out of the box

**Effort:** 2-3 days. Significant refactor but eliminates the lock/manifest complexity that causes reprocessing bugs.

### Option 4: Lightweight CLI Dashboard (Immediate)

Add a script that queries DynamoDB and ECS to show current pipeline state:

```bash
python3 scripts/pipeline_status.py
```

Output:
```
Pipeline Status (2026-05-23 13:45 UTC)
═══════════════════════════════════════
Phase 1 (Parse):    IDLE     (no lock, no task)
Phase 2 (Extract):  RUNNING  (lock: 1716480000, task: arn:aws:ecs:...)
Phase 3 (Enrich):   BLOCKED  (dedup review pending)

Pending Work:
  Content queue:  0 files
  Parsed queue:   3 files (chapter4, chapter5, chapter6)
  Batch jobs:     1 pending (batch_abc123, phase2, 45 reqs, submitted 2h ago)

Last Notifications:
  Phase 2 complete: 2026-05-22 18:30 UTC
  Dedup review:     NOT COMPLETE
```

**Effort:** 2-3 hours. No infrastructure changes.

### Recommendation

**Start with Option 4** (CLI status script) — gives you immediate visibility into why old data is being reprocessed. Then implement **Option 2** (status page in the existing dedup UI) for ongoing monitoring. Consider **Option 3** (Step Functions) only if the lock/manifest orchestration continues to cause issues after fixing the Phase 1 full-sync fallback.

---

## Immediate Fixes to Prevent Reprocessing

1. **Add skip logic to Phase 1**: Compare source markdown mtime against existing parsed JSON mtime. Only re-parse if source is newer.

2. **Scope Phase 1 full-sync fallback**: When no manifest exists, use `BOOK_NAME` env var to limit download to the triggering book only. If no book name, log a warning and refuse to do a full sync (require explicit override).

3. **Fix `_check_pending_content` ordering**: Publish to SNS first, delete DynamoDB entry only after successful publish.

4. **Don't clear all locks in Phase 1**: Only clear the Phase 1 lock. Phase 2/3 locks should only be cleared by the stale lock detection mechanism (which verifies no task is running).

5. **Add a `last_parsed_hash` field**: Store a hash of the source markdown in the parsed JSON. On re-parse, compare hashes and skip upload if unchanged (prevents S3 notification cascade).
