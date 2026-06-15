# Code Integrity Review

**Date:** 2026-06-13  
**Scope:** All source code reviewed for data integrity issues

---

## Critical

### 1. Race condition on index.json (read-modify-write without lock)
- **File:** `src/extraction/batch_parallel.py`
- **Issue:** Multiple async tasks sharing `output_root` can load stale index.json copies, causing last-writer-wins and dropped index entries.
- **Fix:** Use `locked_json()` around the full read-modify-write cycle of index.json.

### 2. BackgroundSync uploads partially-written files
- **File:** `ecs_entrypoint.py` (`_sync_changed`)
- **Issue:** S3 sync thread can read files mid-write (between mkstemp and os.replace).
- **Fix:** Skip `.tmp` files. Add mtime staleness check (only upload if unchanged for >2s).

### 3. DynamoDB dual-write is fire-and-forget
- **File:** `src/utils/file_lock.py` (`_dual_write_dynamo`)
- **Issue:** Failed DynamoDB writes logged at DEBUG only. Causes filesystem/DynamoDB divergence that Phase 3 materialization doesn't detect.
- **Fix:** Log at WARNING. Track failed writes for retry. Add reconciliation at Phase 3 start.

---

## High

### 4. Event mention dedup only checks Sub_eventID
- **File:** `src/extraction/batch_parallel.py`
- **Issue:** Re-extractions with new Sub_eventIDs bypass the duplicate check, accumulating mentions.
- **Fix:** Check (EventID + book + paragraph context) not just Sub_eventID.

### 5. ULID fix generates different replacements for same invalid ID
- **File:** `src/utils/json_validator.py`
- **Issue:** Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity.
- **Fix:** Build replacement map and reuse same new ULID for repeated occurrences.

### 6. merge_generic doesn't update event file cross-references
- **File:** `src/dedup/merge.py`
- **Issue:** After merging places/groups/equipment, event files still reference deleted entity IDs.
- **Fix:** Call `update_event_refs()` in `merge_generic`.

### 7. Phase 1 chapter splitting has no overlap
- **File:** `phase1_parse.py`
- **Issue:** Chapters split at 50 paragraphs with no overlap. Events spanning the boundary generate duplicates or lose context.
- **Fix:** Add 2-3 paragraph overlap between chunks.

---

## Medium

### 8. Date range filter silently rejects pre-1919 dates
- **File:** `src/extraction/batch_parallel.py`
- **Issue:** Dates outside 1919-1955 are silently skipped. Sources reference earlier dates.
- **Fix:** Log rejected dates at WARNING. Consider expanding range.

### 9. Truncated JSON repair hides data loss
- **File:** `src/utils/json_validator.py`
- **Issue:** When LLM truncates, closing braces produces valid but incomplete JSON. Downstream processors don't know data is missing.
- **Fix:** Flag truncated responses as incomplete. Re-request with shorter input.

### 10. No post-extraction entity ID verification
- **File:** `src/extraction/batch_parallel.py`
- **Issue:** ULIDs written into event Sub-events aren't verified to exist as entity files.
- **Fix:** Post-extraction step to check referenced IDs exist.

### 11. String replacement in update_event_refs can corrupt data
- **File:** `src/dedup/merge.py`
- **Issue:** Blind `text.replace(old_id, new_id)` could match IDs appearing in URLs or text fields.
- **Fix:** Parse JSON and only replace in known ID fields.

### 12. Manifest update has race condition
- **File:** `lambda_handlers/trigger_handler.py`
- **Issue:** Concurrent Lambda invocations can overwrite each other's manifest updates.
- **Fix:** Use DynamoDB `list_append` instead of S3 read-modify-write.

### 13. ThreadPoolExecutor context var inheritance
- **File:** `phase2_extract.py`
- **Issue:** `current_book` context var may not propagate correctly to thread workers.
- **Fix:** Use `contextvars.copy_context().run()` in thread target.

### 14. DynamoEntityStore.query_unenriched does full table scan
- **File:** `src/utils/entity_store.py`
- **Issue:** FilterExpression with `begins_with` on partition key = full scan. Slow with 30K+ entities.
- **Fix:** Add GSI on `entity_type` + `enrichment_status`.

### 15. Optional extractors lack idempotency check
- **File:** `phase2_extract.py`
- **Issue:** On re-run, casualties/logistics extract again for all events, creating duplicates.
- **Fix:** Check if output exists for each event before API call.

---

## Low

### 16. O(n²) footnote paragraph detection
- **File:** `src/extraction/events.py`

### 17. Place filename normalization inconsistency
- **File:** `src/extraction/places.py`

### 18. Fulltext reconstruction failure logged at DEBUG not WARNING
- **File:** `src/extraction/events.py`

### 19. Auto-merge doesn't update DynamoDB
- **File:** `ecs_entrypoint.py`

### 20. No schema version check on DynamoDB entity deserialization
- **File:** `src/utils/entity_store.py`
