# Pipeline Review: Transitions, Notifications & Efficiency

**Date:** 2026-05-23  
**Scope:** Phase transitions, notification mechanisms, data processing efficiency

## Overall Assessment

This is a well-architected pipeline with thoughtful incremental processing, cost-conscious infrastructure lifecycle management, and solid resilience patterns. The dual-mode design (local/AWS) is cleanly separated. Below are findings organized by three focus areas.

---

## 1. Phase Transitions

### SUGGESTION: Local mode has no automated phase chaining

In local mode, each phase is invoked manually (`python3 phase1_parse.py`, then `phase2_extract.py`, etc.). There's no orchestrator that chains them. The retry wrappers (`phase2_retry.py`, `phase3_retry.py`) only retry within a single phase — they don't trigger the next phase on success.

This is fine for a developer-driven workflow, but if you ever want unattended local runs (e.g., a cron job processes new content end-to-end), you'd need a simple `run_pipeline.py` that sequences Phase 1 → Phase 2 → Phase 3.

### SUGGESTION: Dedup gate is event-driven but has a timing gap

The `dedup_gate_handler.py` checks `dedup/review_status.json` in S3. But the dedup review UI sets `complete: true` and publishes to SNS. If the SNS message is missed (Lambda throttled, DLQ), Phase 3 never starts. There's no periodic reconciliation — unlike the stale lock detection (hourly EventBridge), there's no equivalent "check if dedup is done but Phase 3 never ran."

**Recommendation:** Add a check in the hourly EventBridge rule: if `review_status.complete == true` AND no Phase 3 lock exists AND no Phase 3 ECS task is running, trigger Phase 3.

### WARNING: Phase 3 lock is never removed by `_post_process`

In `ecs_entrypoint.py`:
```python
if "phase3" not in phase_script:
    _remove_lock(phase_script)
```

Phase 3's lock is intentionally not removed in `_post_process`. This means Phase 3 relies on the stale lock detection mechanism (hourly EventBridge) to clean up after itself. If Phase 3 completes successfully, its lock persists until the next Phase 1 run calls `_clear_all_locks()` or the hourly check finds no running task.

This is presumably intentional (to prevent re-triggering Phase 3 from entity file uploads), but it means if you want to re-run Phase 3 manually, you must clear the lock first. Worth documenting explicitly.

### SUGGESTION: Pending content queue re-trigger is fragile

In `_check_pending_content()`, the re-trigger writes a manifest to S3 then publishes to SNS. If the SNS publish fails after the DynamoDB delete, the pending content is lost. Consider:
```python
# Publish first, delete after confirmation
sns.publish(...)
table.delete_item(...)  # Only after successful publish
```

---

## 2. Notifications

### SUGGESTION: No failure notifications from ECS tasks

`_notify_complete()` only fires on success. If a phase fails (non-zero exit code), the entrypoint logs the error and calls `sys.exit(result.returncode)` — but never sends an SNS notification. The operator only learns about failures by checking CloudWatch Logs or ECS task status.

**Recommendation:** Add a `_notify_failure(phase_script, returncode)` call in the error path of `run_phase()`:
```python
if result.returncode != 0:
    logger.error("Phase script exited with code %d", result.returncode)
    _notify_failure(phase_script, result.returncode)  # <-- add this
    _final_sync(phase_script)
    ...
```

### SUGGESTION: Batch poller notifications are minimal

The batch poller sends notifications for completion and failure, but not for "batch has been pending for >X hours." A long-running batch (>6h) with no progress notification leaves the operator blind. Consider a "still waiting" notification after a configurable threshold.

### SUGGESTION: Phase 1 completion notification doesn't include context

The Phase 1 notification lists parsed filenames but doesn't indicate whether this is a full re-parse or incremental. Adding "Incremental: 3 new files" vs "Full re-parse: 47 files" would help operators understand what triggered the run.

---

## 3. Data Processing Efficiency

### WARNING: `_get_book_entity_files` downloads all entity files anyway

In `ecs_entrypoint.py`, the function attempts to scope downloads to a specific book's entities. But the ID→filename mapping logic is commented out with `pass`, and the fallback downloads the entire `index.json` for every entity type, then downloads ALL files listed in those indexes (not just book-specific ones). The "scoped download" effectively downloads everything.

```python
for ref_id in sub.get(field, []):
    # Entity files are named by normalized name, not ID
    # We can't map ID→filename without index, so download all for referenced types
    pass
```

**Impact:** Phase 3 with `BOOK_NAME` set still downloads the full entity corpus. For a project with thousands of entity files across many books, this negates the scoping optimization.

**Recommendation:** Add a `book` or `source_book` field to entity files during Phase 2 extraction, then filter the index by that field during scoped downloads.

### WARNING: Background sync uploads entity files that haven't changed

`BackgroundSync._sync()` uploads all files in `output/` entity dirs every 120 seconds, excluding only parsed/event files and previously-downloaded keys. But it doesn't track which files were actually modified since the last sync. If Phase 2 is processing chapter 5 but chapters 1-4's entities are already on disk, those get re-uploaded every 2 minutes.

**Recommendation:** Track file mtimes or use a dirty-set that extraction code marks when it writes a file.

### SUGGESTION: Phase 2 optional entity extraction is sequential

In `_run_core_extraction`, the core extraction (events, dates, places, people) runs in parallel via `process_chapters_parallel`. But optional entities (weather, equipment, logistics, casualties, supplemental) are extracted sequentially per event file in a simple `for` loop:

```python
for event_file in event_files:
    _extract_optional_entities(event_file, ...)
```

Each optional extractor makes independent API calls. These could run in parallel across event files (same `max_parallel` semaphore as core extraction) for significant speedup when multiple optional extractors are enabled.

### SUGGESTION: Phase 3 retry wrapper checks only `enrichment_data` field

`phase3_retry.py` counts unenriched people by checking `data.get("enrichment_data")`. But the pipeline also uses `enrichment_status: "not_found"` to mark entities that were searched but had no results. These are counted as "unenriched" by the retry wrapper, causing unnecessary retries that will always find the same nothing.

**Recommendation:** Also check for `enrichment_status` in the retry wrapper:
```python
if not data.get("enrichment_data") and not data.get("enrichment_status"):
    unenriched += 1
```

### SUGGESTION: Dedup downloads full entity corpus for cross-book matching

`_run_dedup_detection` downloads ALL entity files from S3 (`_download_s3_prefix_skip_existing` for people, places, groups, equipment, plus all event files). For a large corpus this is a significant download. Since dedup only needs names and IDs for comparison (not full entity data), downloading just the index files + a lightweight dedup-specific index would be more efficient.

### SUGGESTION: Schema migration stamps all files on every Phase 3 run

`_stamp_schema_versions()` iterates every JSON file in every entity directory. The early-exit optimization (checking one sample file) helps, but if even one file needs migration, it scans everything. For incremental runs processing a single book, this is disproportionate work.

---

## Summary of Priorities

| Priority | Finding | Impact |
|----------|---------|--------|
| WARNING | No failure notifications from ECS tasks | Operator blindness on failures |
| WARNING | `_get_book_entity_files` scoping doesn't actually scope | Unnecessary S3 downloads |
| WARNING | Background sync re-uploads unchanged files | Wasted S3 PUT requests and bandwidth |
| SUGGESTION | No dedup-gate reconciliation on missed SNS | Phase 3 could stall indefinitely |
| SUGGESTION | Optional entity extraction is sequential | Slower Phase 2 when multiple extractors enabled |
| SUGGESTION | Phase 3 retry counts `not_found` as unenriched | Wasted retry cycles |
| SUGGESTION | Pending content re-trigger can lose data | Race condition on failure |
| SUGGESTION | No local-mode phase chaining | Manual intervention required |

The architecture is solid — the incremental processing, manifest-based data passing, and infrastructure lifecycle management are well-designed. The issues above are refinements rather than fundamental problems.
