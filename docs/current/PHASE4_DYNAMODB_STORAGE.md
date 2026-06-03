# Phase 4: DynamoDB as Primary Entity Storage

**Status:** Design  
**Date:** 2026-06-03  
**Priority:** High — prevents spot termination data loss, eliminates 15-30 min download phase

---

## Problem

Current architecture stores entities as JSON files in S3. Every pipeline run:
1. Downloads ALL entity files (15-30 min for 12,500+ files)
2. Processes entities locally
3. Background sync uploads changes every 120s
4. Spot termination can lose up to 2 min of work (no immediate durability)

---

## Solution: DynamoDB as Source of Truth

**Strategy:** Dual-write. DynamoDB for operational reads/writes, S3 for archival/export.

```
Write path:  Entity → DynamoDB (immediate, durable) → S3 (periodic export)
Read path:   DynamoDB query → entity data (no bulk download needed)
```

---

## DynamoDB Schema

### Single-table design (existing `dev-wwii-api-cache` table)

**Entity items:**
```
PK: entity#{entity_type}#{entity_id}
entity_type: people|places|dates|equipment|weather|logistics|casualties|people_groups|bibliography|maps
entity_id: ULID (26 chars)
data: JSON string of full entity
name: indexed name (for lookups)
enrichment_status: null|enriched|not_found
book: source book name
created_at: ISO timestamp
updated_at: ISO timestamp
```

**GSI (Global Secondary Index):**
- `entity_type-enrichment_status-index`: Query unenriched entities by type
- `entity_type-name-index`: Query by normalized name (for dedup)
- `entity_type-book-index`: Query entities by source book

---

## Implementation Phases

### Phase A: Core abstraction (this sprint)
- Create `src/utils/entity_store.py` with `DynamoEntityStore` class
- Implement: `get(type, id)`, `put(type, id, data)`, `query_unenriched(type)`, `query_by_name(type, name)`
- Add `storage.entity_backend: "dynamodb"|"filesystem"` to config.yaml
- Dual-write in `write_json_with_lock`: write file AND DynamoDB

### Phase B: Read path migration
- Phase 3 enrichment reads from DynamoDB instead of downloading files
- `enrich_all_people` → `query_unenriched("people")` instead of glob + filter
- Eliminates the 15-30 min download entirely
- Background sync becomes S3 export (write-only, not bidirectional)

### Phase C: Dedup migration
- Dedup scripts query DynamoDB instead of loading all files
- Incremental dedup: query entities with `created_at > last_dedup_run`
- Distance matching uses `places_coords` already in DynamoDB

### Phase D: Full migration
- Phase 2 writes entities directly to DynamoDB (no local files)
- S3 export runs as a scheduled job or on phase completion
- Remove file download logic from `ecs_entrypoint.py`

---

## Cost Estimate

- **Storage:** 12,500 entities × ~2KB avg = ~25MB in DynamoDB. PAY_PER_REQUEST = ~$0.25/month
- **Reads:** ~50,000 reads/run × $0.25/million = ~$0.01/run
- **Writes:** ~5,000 writes/run × $1.25/million = ~$0.006/run
- **GSI:** Adds ~20% storage cost = ~$0.05/month

Total: <$1/month. Dramatically cheaper than the compute cost of 15-min file downloads.

---

## Migration Strategy

1. Enable dual-write (file + DynamoDB) — no behavior change, builds DynamoDB copy
2. Once DynamoDB is populated, switch reads to DynamoDB
3. After validation, remove file-download-before-processing pattern
4. S3 becomes export-only (periodic dump for human review)

---

## Backward Compatibility

- `aws.enabled: false` (local mode) → filesystem only, no change
- `aws.enabled: true` + `storage.entity_backend: "filesystem"` → current behavior
- `aws.enabled: true` + `storage.entity_backend: "dynamodb"` → new behavior

Default: `"filesystem"` until migration is validated.
