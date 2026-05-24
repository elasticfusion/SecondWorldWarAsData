# Spot Instance Recovery Analysis

**Date:** 2026-05-24  
**Context:** ECS Fargate tasks switched to SPOT capacity provider

---

## How Spot Termination Works

ECS Fargate Spot sends SIGTERM 30 seconds before killing the task. The task's `stoppedReason` is set to `"Your Spot Task was interrupted"`. After 30 seconds, SIGKILL is sent unconditionally.

---

## Current State: What Survives

| Component | Persisted? | Mechanism | Max Data Loss |
|-----------|-----------|-----------|---------------|
| Grok API responses | ✅ Yes | DynamoDB cache (written immediately per call) | None |
| Entity files (people, places, etc.) | ⚠️ Partial | Background S3 sync every 120s | Up to 2 min of work |
| Event files (-event.json) | ❌ No | Excluded from background sync | All since task start |
| Parsed files (-parsed.json) | ❌ No | Excluded from background sync | All since task start |
| Index files (index.json) | ⚠️ Partial | Synced with entity files | Up to 2 min |
| DynamoDB locks | ⚠️ Stale | Persists until hourly cleanup | Up to 1 hour blocked |
| Batch job state | ✅ Yes | DynamoDB (`batch_job#` entries) | None |
| Dedup reports | ❌ No | Only uploaded in `_post_process` | All |

---

## Current Recovery Path

1. Spot task killed → no final sync occurs (no SIGTERM handler)
2. DynamoDB lock persists → pipeline blocked
3. Hourly EventBridge stale-lock check → detects no running task → clears lock (up to 1 hour delay)
4. Trigger Lambda launches new task
5. New task downloads from S3 → gets entity files from last background sync
6. Phase 2 S3 scan finds parsed files without event files → re-extracts
7. All Grok API calls hit DynamoDB cache → regeneration is fast and free
8. Entity extraction matches existing index entries → no duplicates created

**Net cost of a spot termination:**
- Up to 1 hour of pipeline downtime (stale lock)
- Up to 2 minutes of entity file updates lost (re-generated from cache on next run)
- Event files regenerated from cache (seconds per chapter, no API cost)
- No duplicate entities (index-based dedup handles re-extraction)

---

## Gaps

### 1. No SIGTERM Handler (Critical)

The entrypoint has no signal handling. ECS gives 30 seconds of warning before kill — enough time to upload all pending work to S3. Currently this window is wasted.

```python
# Not present in ecs_entrypoint.py:
signal.signal(signal.SIGTERM, handler)
```

### 2. Event Files Excluded from Background Sync

```python
exclude = ["-parsed.json", "-event.json"]
```

Event extraction is the most expensive operation (largest API calls, minutes per chapter). These files are only uploaded during `_final_sync` which never runs on spot termination. The exclusion was originally to prevent S3 notifications re-triggering Phase 2, but event files have a `-event.json` suffix — the S3 notification is only configured for `-parsed.json` suffix. **Event files are safe to background-sync.**

### 3. Stale Lock Blocks Pipeline for Up to 1 Hour

The hourly EventBridge rule is the only mechanism to detect a killed task and clear its lock. During this window, new content uploads are queued but not processed.

### 4. No Spot-Aware Detection in Trigger Lambda

The trigger Lambda's stale lock check uses `ecs.list_tasks(desiredStatus="RUNNING")` — this correctly detects the task is gone. But it only runs hourly. There's no immediate detection of spot interruption.

---

## Recommended Fixes

### Fix 1: Add SIGTERM Handler (Critical, 15 min)

```python
import signal

_phase_script_global = ""

def _handle_sigterm(signum, frame):
    """Emergency sync on spot termination — 30 second window."""
    logger.warning("SIGTERM received — performing emergency S3 sync")
    try:
        _final_sync(_phase_script_global)
        _remove_lock(_phase_script_global)
        logger.info("Emergency sync complete")
    except Exception as e:
        logger.error("Emergency sync failed: %s", e)
    sys.exit(143)

signal.signal(signal.SIGTERM, _handle_sigterm)
```

Set `_phase_script_global` at the start of `run_phase()`. This gives a clean shutdown: uploads all work, clears the lock, and exits. The 30-second window is sufficient for uploading entity files (typically < 100 files, < 10MB total).

### Fix 2: Include Event Files in Background Sync (10 min)

Remove `-event.json` from the exclude list in `BackgroundSync._sync_changed`:

```python
exclude = ["-parsed.json"]  # Keep parsed excluded (triggers Phase 2)
# -event.json is safe — S3 notification only fires for -parsed.json suffix
```

Event files are 10-50KB each. Syncing them every 120s adds negligible S3 cost but saves the most expensive work from being lost.

### Fix 3: Reduce Sync Interval for Spot (5 min)

Set environment variable in the ECS task definition:

```yaml
Environment:
  - Name: SYNC_INTERVAL
    Value: "60"
```

Halves the maximum data loss window from 2 minutes to 1 minute. Cost: ~30 extra S3 PUT requests per hour (~$0.0002/hr).

### Fix 4: Spot-Aware Lock Clearing (30 min)

Add spot interruption detection to the trigger Lambda's stale lock check:

```python
# In scheduled lock check:
for task_def, family in TASK_FAMILIES.items():
    lock = dynamo.get_item(Key={"cache_key": f"lock#{family}"}).get("Item")
    if not lock:
        continue
    running = ecs.list_tasks(cluster=CLUSTER, family=family, desiredStatus="RUNNING")
    if running.get("taskArns"):
        continue
    # Check if last task was spot-interrupted
    stopped = ecs.list_tasks(cluster=CLUSTER, family=family, desiredStatus="STOPPED")
    if stopped.get("taskArns"):
        desc = ecs.describe_tasks(cluster=CLUSTER, tasks=stopped["taskArns"][:1])
        reason = desc["tasks"][0].get("stoppedReason", "")
        if "Spot" in reason:
            logger.info("Spot interruption detected for %s — clearing lock", family)
    dynamo.delete_item(Key={"cache_key": f"lock#{family}"})
```

Alternatively, reduce the EventBridge schedule from `rate(1 hour)` to `rate(5 minutes)` — simple and effective. Cost: ~$0.01/month for the extra Lambda invocations.

### Fix 5: ECS Task State Change Event (Best, 30 min)

Instead of polling, use an EventBridge rule that triggers on ECS task state changes:

```yaml
EventPattern:
  source: ["aws.ecs"]
  detail-type: ["ECS Task State Change"]
  detail:
    clusterArn: [!GetAtt Cluster.Arn]
    lastStatus: ["STOPPED"]
    stoppedReason: [{prefix: "Your Spot Task"}]
```

Target: the trigger Lambda. This gives **immediate** lock clearing on spot termination — zero downtime.

---

## After All Fixes: Recovery Timeline

| Event | Time | Action |
|-------|------|--------|
| Spot termination notice | T+0s | SIGTERM received |
| Emergency sync | T+1-10s | All entity + event files uploaded to S3 |
| Lock cleared | T+10s | `_remove_lock` in SIGTERM handler |
| Process killed | T+30s | SIGKILL (already exited cleanly) |
| EventBridge fires | T+0s | ECS task state change → trigger Lambda |
| New task launches | T+60-120s | NAT creation + task startup |
| Pipeline resumes | T+120-180s | Downloads from S3, hits cache, continues |

**Total downtime: ~3 minutes** (vs current ~1 hour)  
**Data loss: zero** (SIGTERM handler syncs everything)  
**API cost: zero** (DynamoDB cache serves all re-requests)

---

## Implementation Priority

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| 1 | SIGTERM handler | 15 min | Eliminates all data loss |
| 2 | Include event files in background sync | 10 min | Safety net if SIGTERM handler fails |
| 3 | Reduce sync interval to 60s | 5 min | Reduces loss window |
| 4 | EventBridge task state change rule | 30 min | Reduces downtime from 1hr to seconds |
| 5 | Reduce stale lock check to 5 min (alternative to #4) | 5 min | Reduces downtime to 5 min |
