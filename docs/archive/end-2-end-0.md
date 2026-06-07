# End-to-End Run #0 — Issues Found

**Date:** 2026-05-25  
**Book:** TheLorraineCampaign  
**Status:** Issues 2, 3, 4 fixed (2026-05-25). Issue 1 (notifications) tracked in TODO.md Medium Priority.

---

## Issue 1: No Email Notification on Phase 2 Launch

**Symptom:** No email received when Phase 2 ECS task was launched.

**Root Cause:** `_run_task()` in `lambda_handlers/trigger_handler.py` launches the ECS task but sends no notification. Notifications only exist for:
- Pipeline busy ("Content queued") — in `_launch_phase1_if_idle` only
- Phase completion — in `ecs_entrypoint.py` `_notify_complete` (after phase finishes)
- Phase failure — in `ecs_entrypoint.py` `_notify_failure`

There is no "Phase X started" notification anywhere in the codebase.

**Fix:** Add `_notify_launch()` at the end of `_run_task()`:

```python
# At end of _run_task(), after ecs.run_task():

def _notify_launch(family: str, source: str) -> None:
    """Send SNS notification when a pipeline task is launched."""
    if not NOTIFY_TOPIC:
        return
    phase_names = {
        f"{ENV_NAME}-wwii-phase1-parse": "Phase 1 (Parse)",
        f"{ENV_NAME}-wwii-phase2-extract": "Phase 2 (Extract)",
        f"{ENV_NAME}-wwii-phase3-enrich": "Phase 3 (Enrich)",
    }
    phase_name = phase_names.get(family, family)
    try:
        boto3.client("sns").publish(
            TopicArn=NOTIFY_TOPIC,
            Subject=f"WWII Pipeline: {phase_name} launched",
            Message=f"{phase_name} ECS task launched.\nTrigger: {source}\nCluster: {CLUSTER}",
        )
    except Exception as e:
        logger.warning("Failed to send launch notification: %s", e)
```

Then call it at the end of `_run_task`:
```python
    ecs.run_task(...)
    _notify_launch(family, source)
```

**File:** `lambda_handlers/trigger_handler.py`, after line ~210 (the `ecs.run_task` call)

---

## Issue 2: No Notification That Batch Completed and Dedup Stage Reached

**Symptom:** After Phase 2 batch submission, no email received indicating batch completion or that dedup review is needed.

**Root Cause:** Two compounding issues:

### 2a. Retrieve Task Can't Find Metrics File (Likely Failure)

The retrieve-only flow needs `output/metrics/batch_*.json` to map `request_id → cache_type`. This file is uploaded by the submit-only task's `_final_sync`, but `_download_phase2_inputs()` (called by the retrieve task) does NOT download the `output/metrics/` prefix.

Without the metrics file, all batch results are cached under `"default"` type. When the phase re-runs, it looks in type-specific caches (`events`, `people`, `places`, etc.) and finds nothing — causing the re-run to fail or produce empty results.

**File:** `ecs_entrypoint.py`, `run_retrieve_only` (line ~1400)

**Fix:** Download metrics before populating cache:
```python
# After _download_inputs(phase_script), add:
_download_s3_prefix(s3, "output/metrics/")
```

Or more targeted:
```python
# Download metrics for this batch
s3 = _s3_client()
for page in s3.get_paginator("list_objects_v2").paginate(
    Bucket=BUCKET, Prefix="output/metrics/"
):
    for obj in page.get("Contents", []):
        _download_s3_file(s3, obj["Key"])
```

### 2b. No "Batch Submitted" Notification from Submit-Only Task

The submit-only flow exits silently after enqueuing the batch job. No notification is sent to indicate:
- The batch was submitted successfully
- How many requests are in the batch
- Expected wait time

The batch poller sends a notification when the batch **completes** (up to hours later), but there's no confirmation that submission worked.

**Fix:** Add notification at end of `run_submit_only`:
```python
# After _enqueue_from_metrics(phase_script):
try:
    sns = boto3.client("sns", region_name=REGION)
    sns.publish(
        TopicArn=os.environ.get("NOTIFICATION_TOPIC_ARN", ""),
        Subject=f"WWII Pipeline: Phase 2 batch submitted",
        Message=f"Batch submitted to Grok API.\nInfra torn down. Poller will check every 15 min.",
    )
except Exception:
    pass
```

---

## Issue 3: ECS Container and Network Components Not Torn Down After Phase 2

**Symptom:** After Phase 2 completed, NAT Gateway and VPC endpoints remained running.

**Root Cause:** Three compounding factors:

### 3a. `_post_process` Doesn't Call `_teardown_networking()`

`_post_process` (called after successful phase completion) calls `_stop_openserp` and `_remove_lock` but NOT `_teardown_networking()`. Teardown is only called in `run_submit_only`.

**File:** `ecs_entrypoint.py`, `_post_process` (line ~742)

### 3b. `nat_manager` Ignores Phase 2 Completion SNS

The SNS subscription on `Phase2CompleteTopic` invokes `nat_manager`, but the handler explicitly ignores non-Phase-3 messages:

```python
if "Phase 3" in message and "completed successfully" in message:
    return _delete_all(ec2, region)
logger.info("Ignoring SNS (not Phase 3 completion): %s", message[:80])
return {"action": "none"}
```

This was intentional (Phase 3 needs networking), but in batch mode where Phase 3 won't run for hours, the NAT sits idle.

**File:** `lambda_handlers/nat_manager.py`, line ~37

### 3c. Retrieve Task Likely Failed (Issue 2)

If the retrieve task failed before `_post_process`, no notification was sent at all, so even the SNS→nat_manager path was never triggered.

### Fix Options

**Option A (Recommended): Delayed teardown via EventBridge scheduled rule**

After Phase 2 completes, schedule a one-shot EventBridge rule to invoke `nat_manager(delete)` in 30 minutes. If Phase 3 launches before then (dedup review completed quickly), the trigger Lambda cancels the scheduled teardown by deleting the rule.

```python
# In _post_process, after _notify_complete for Phase 2:
if "phase2" in phase_script:
    _schedule_delayed_teardown(delay_minutes=30)
```

```python
def _schedule_delayed_teardown(delay_minutes: int = 30) -> None:
    """Schedule networking teardown after a delay to avoid churn."""
    import datetime
    events = boto3.client("events", region_name=REGION)
    env = os.environ.get("ENV_NAME", "dev")
    rule_name = f"{env}-wwii-delayed-teardown"
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=delay_minutes)
    try:
        events.put_rule(
            Name=rule_name,
            ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            State="ENABLED",
        )
        events.put_targets(
            Rule=rule_name,
            Targets=[{
                "Id": "nat-teardown",
                "Arn": f"arn:aws:lambda:{REGION}:{_get_account_id()}:function:{env}-wwii-nat-manager",
                "Input": '{"action": "delete"}',
            }],
        )
        logger.info("Scheduled networking teardown in %d minutes", delay_minutes)
    except Exception as e:
        logger.warning("Failed to schedule delayed teardown: %s", e)
```

Then in the trigger Lambda's `_run_task`, cancel any pending teardown:
```python
# At start of _run_task, before creating networking:
try:
    events = boto3.client("events")
    events.remove_targets(Rule=f"{ENV_NAME}-wwii-delayed-teardown", Ids=["nat-teardown"])
    events.delete_rule(Name=f"{ENV_NAME}-wwii-delayed-teardown")
except Exception:
    pass  # Rule may not exist
```

This avoids churn: if Phase 3 or Phase 1 launches within 30 minutes, networking stays up. If nothing happens, it tears down automatically.

**Simpler alternative:** Have `nat_manager` handle Phase 2 completion with a 30-minute delay built into its idle monitor (already runs every 10 minutes). Change the SNS handler to set a "teardown_after" timestamp in DynamoDB, and the idle monitor checks if the timestamp has passed before tearing down.

**Cost impact of the bug:** NAT Gateway ($0.045/hr) + 3 VPC Interface Endpoints (~$0.01/hr each) = ~$0.075/hr idle. If left running overnight = ~$1.35 wasted.

---

## Issue 4: 440 Batch Submissions Despite "Fast Run" — Full Book Reprocessed

**Symptom:** Intended as a single-event test run, but 440 requests submitted to Grok Batch API. Screenshot shows 5 identical `phase2-TheLorraineCampaign-89files-440reqs` batches over 4 days.

**Root Cause:** Cascading failure from Issue 2:

1. **Retrieve task fails** (can't find metrics file → cache populated under wrong type → re-run fails)
2. **Event files never uploaded to S3** (retrieve task exits before `_final_sync`)
3. **Next submit-only run** calls `_download_phase2_inputs` → S3 scan finds 89 parsed files with no event files → downloads all 89
4. **Phase 2 runs on all 89 files** → event extraction hits DynamoDB cache (fast) → entity extraction builds prompts → checks cache
5. **Cache misses** because prompts were modified between runs (May 24: `hpPrompt`, `mpPromptschema`, `batch_parallel.py` changes) → 440 new requests submitted
6. **Repeat** on every subsequent run

### Why the Cache Doesn't Help

The cache key is:
```python
content = f"{prompt}:{temperature}:{self.model}"
key = hashlib.sha256(content.encode()).hexdigest()
```

Any change to the prompt text (even whitespace) produces a completely different hash. Between May 20-24, prompts were modified multiple times, invalidating all cached responses.

### Why Skip Logic Doesn't Help

The S3-level skip (`head_object` for event files) only works if event files were successfully uploaded. Since the retrieve task keeps failing, event files never reach S3, and every run looks like a fresh extraction.

### The Feedback Loop

```
Submit-only → batch enqueued → retrieve fails → no event files in S3
    ↓
Next trigger → S3 scan finds all 89 files → submits again (440 reqs)
    ↓
Retrieve fails again → repeat
```

### Fix

The primary fix is **Issue 2** (download metrics in retrieve-only flow). Once retrieve succeeds:
- Event files get uploaded to S3
- Next run's S3 scan sees event files exist → skips those chapters
- Only truly new/changed chapters get submitted

### Additional Safeguard: Dedup Batch Submissions

Before submitting a batch, check if an identical batch (same book, same file count) is already pending or recently completed:

```python
# In run_submit_only, before _enqueue_from_metrics:
existing_jobs = _get_recent_jobs(phase="phase2", book=book_name, hours=24)
if existing_jobs:
    logger.warning(
        "Skipping batch submission — %d recent job(s) for %s already exist",
        len(existing_jobs), book_name
    )
    return
```

### Cost Impact

Each redundant batch: 440 requests × ~$0.003/req = ~$1.32. Five redundant batches = ~$6.60 wasted (plus the 882-request optional batch at ~$2.65).

---

## Issue 4 Addendum: All Books Processed, Not Just TheLorraineCampaign

**Symptom:** Batch name says `TheLorraineCampaign-89files` but the run was intended for one book only.

**Root Cause:** The trigger Lambda's `_run_task()` does NOT pass `BOOK_NAME` as an environment override to the ECS task. The Phase 2 task definition also doesn't set it. So `BOOK_NAME` is empty at runtime.

With `BOOK_NAME=""`, the S3 scan prefix becomes `output/content/` (all books):
```python
book_name = os.environ.get("BOOK_NAME", "")
scan_prefix = f"output/content/{book_name}/" if book_name else "output/content/"
```

This means the 89 parsed files likely span all three books (TheLorraineCampaign + CrossChannelAttack + BreakoutAndPursuit). The batch name only shows "TheLorraineCampaign" because the naming logic uses the first book found.

**Fix:** The trigger Lambda should extract the book name from the queued S3 keys and pass it as `BOOK_NAME`:

```python
# In trigger_handler.py _run_task, add container overrides:
def _run_task(task_def, source, book_name=""):
    ...
    overrides = {}
    if book_name:
        overrides = {
            "containerOverrides": [{
                "name": "pipeline",
                "environment": [{"name": "BOOK_NAME", "value": book_name}],
            }]
        }
    ecs.run_task(..., overrides=overrides)
```

And extract book name from the pending queue:
```python
# In _launch_phase2_if_idle:
resp = dynamo.get_item(Key={"cache_key": "pending#parsed"})
keys = resp.get("Item", {}).get("keys", [])
books = set(k.split("/")[2] for k in keys if k.startswith("output/content/"))
# Launch one task per book, or pass the first book
```

---

## Feature Request: Separate Batch Jobs for New vs Revised Content

**Context:** When prompts change, the entire corpus needs re-extraction (cache invalidated). This should not block or delay processing of genuinely new content.

**Request:** Split batch submissions into two separate jobs:

1. **New content job** — chapters that have never been processed (no event file in S3). High priority, fast turnaround.
2. **Revised content job** — chapters that have existing event files but need re-extraction due to prompt/model changes. Lower priority, can run on a schedule.

**Proposed Behavior:**

```
Phase 2 submit-only:
  1. Scan for parsed files without event files → "new" set
  2. Scan for parsed files WITH event files but cache miss → "revised" set
  3. Submit "new" as immediate batch job (existing flow)
  4. Submit "revised" as separate batch job (or defer to scheduled run)
```

**Benefits:**
- New content gets processed immediately without waiting for full-corpus re-extraction
- Revised content can be batched into off-peak scheduled runs (e.g., nightly)
- Easier to track costs: new content extraction vs prompt-improvement re-runs
- Avoids the 440-request surprise when only 1 new chapter was intended

**Implementation Notes:**
- Add `batch.revision_schedule: "cron(0 2 * * ? *)"` to config.yaml (run revisions at 2 AM)
- The batch poller already handles multiple concurrent jobs per phase
- Job queue already has `book` field — add `job_type: "new" | "revised"` field
- Revised jobs could use a lower-priority capacity provider or smaller task size

---

## New Issue Found During Review

### Issue 5: Delayed Teardown Uses Wrong API — `at()` Not Supported by EventBridge Rules

**File:** `ecs_entrypoint.py`, `_schedule_delayed_teardown` (line ~1295)

```python
events.put_rule(
    ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
    ...
)
```

The `at()` expression is only supported by **EventBridge Scheduler** (`scheduler.create_schedule`), NOT by **EventBridge Rules** (`events.put_rule`). Rules only support `cron()` and `rate()`. This call will fail with `ValidationException: Parameter ScheduleExpression is not valid`.

**Fix:** Use EventBridge Scheduler instead:

```python
def _schedule_delayed_teardown(delay_minutes: int = 30) -> None:
    import datetime
    env = os.environ.get("ENV_NAME", "dev")
    schedule_name = f"{env}-wwii-delayed-teardown"
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=delay_minutes)
    nat_fn_arn = f"arn:aws:lambda:{REGION}:{_get_account_id()}:function:{env}-wwii-nat-manager"
    
    try:
        scheduler = boto3.client("scheduler", region_name=REGION)
        scheduler.create_schedule(
            Name=schedule_name,
            ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": nat_fn_arn,
                "RoleArn": os.environ.get("SCHEDULER_ROLE_ARN", ""),
                "Input": '{"action": "delete"}',
            },
            ActionAfterCompletion="DELETE",  # Auto-cleanup after firing
        )
        logger.info("Scheduled networking teardown in %d minutes", delay_minutes)
    except Exception as e:
        logger.warning("Failed to schedule delayed teardown: %s", e)
```

And update `_cancel_delayed_teardown` in trigger_handler.py:
```python
def _cancel_delayed_teardown():
    try:
        scheduler = boto3.client("scheduler")
        scheduler.delete_schedule(Name=f"{ENV_NAME}-wwii-delayed-teardown")
        logger.info("Cancelled delayed teardown")
    except Exception:
        pass
```

**Note:** EventBridge Scheduler requires a separate IAM role (`SCHEDULER_ROLE_ARN`) with permission to invoke the Lambda. Add to CloudFormation.
