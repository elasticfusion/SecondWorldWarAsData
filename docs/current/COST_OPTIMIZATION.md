# Cost Optimization Recommendations

**Date:** 2026-05-24  
**Current estimated monthly spend:** ~$55-75 (AWS) + Grok API usage

---

## AWS Infrastructure (~$22-33/month savings)

### 1. Move SecretsManager VPC Endpoint to Dynamic Set — $14.60/month

The SecretsManager interface endpoint is always-on in `network.yaml` (2 AZs × $0.01/hr × 730 hrs = $14.60/month) for a service called ~5 times per pipeline run.

**Fix:** Remove from `network.yaml`, add `"secretsmanager"` to `INTERFACE_ENDPOINTS` in `lambda_handlers/nat_manager.py`. It will be created/destroyed with the other dynamic endpoints.

### 2. Downgrade Container Insights — $5-10/month

`containerInsights: enhanced` generates 50-100 custom metrics at $0.01 each. For a batch pipeline running intermittently, standard metrics suffice.

**Fix:** Change `enhanced` → `enabled` in `compute.yaml` ECS cluster definition.

### 3. Add S3 Noncurrent Version Expiration — $1-5/month

Versioning is enabled but old versions never expire, causing unbounded growth.

**Fix:** Add to `storage.yaml` DataBucket lifecycle:
```yaml
- Id: CleanupOldVersions
  Status: Enabled
  NoncurrentVersionExpiration:
    NoncurrentDays: 30
```

### 4. Set Lambda Log Group Retention — $1-3/month

Lambda log groups use default retention (never expire). Logs grow forever.

**Fix:** Add explicit `AWS::Logs::LogGroup` resources for each Lambda with `RetentionInDays: 30`.

### 5. Reduce Lambda Memory — ~$1/month

| Function | Current | Recommended |
|----------|---------|-------------|
| Dedup UI | 512 MB | 256 MB |
| Metrics | 256 MB | 128 MB |
| OpenSERP Manager | 256 MB | 128 MB |

---

## Grok API (~20-35% cost reduction)

### 1. Model Tiering (Highest Impact) — 15-25% savings

`grok-4.3` is used for ALL tasks including trivial classification. Many tasks can use `grok-3-mini-fast` (~10x cheaper per token):

| Task | Cache Type | Why mini is sufficient |
|------|-----------|----------------------|
| ISBN extraction | `supplemental_advanced` | Extract a number from text |
| URL validation | `bibliography_verify` | Yes/no classification |
| License classification | `license_check` | Simple categorization |
| Copyright determination | `supplemental_advanced` | Rule-based classification |
| NARA catalog matching | `bibliography_nara` | Text matching |
| Search query generation | `supplemental_search` | Simple reformulation |
| Image relevance check | `openserp_verify` | Yes/no with context |

**Implementation:** Add model map to `config.yaml`:
```yaml
api:
  grok:
    model: "grok-4.3"
    model_light: "grok-3-mini-fast"
    model_map:
      supplemental_advanced: "grok-3-mini-fast"
      bibliography_verify: "grok-3-mini-fast"
      bibliography_nara: "grok-3-mini-fast"
      openserp_verify: "grok-3-mini-fast"
      license_check: "grok-3-mini-fast"
      supplemental_search: "grok-3-mini-fast"
```

### 2. Reduce Fulltext in Prompts — 10-15% fewer input tokens

Phase 2 sends full chapter text for ALL entity types (dates, places, people, groups). For dates and places extraction, sub-event summaries alone likely suffice.

**Test:** Run dates extraction with `include_fulltext=False` on a sample chapter and compare accuracy. If acceptable, this cuts 25% of Phase 2 input tokens.

### 3. Increase Rate Limit — 50% faster ECS runtime

Current: 30 calls/min. The Grok API supports higher throughput and the code already handles 429 backoff gracefully.

**Fix:** Change `calls_per_minute: 30` → `calls_per_minute: 60` in `config.yaml`. Reduces ECS Fargate runtime (and cost) for non-batch runs.

### 4. Track Processed Event Files — 5-10 min less ECS time

Optional extractors (weather, equipment, logistics, casualties) re-scan ALL event files on every run. Cache prevents duplicate API calls, but DynamoDB lookups and prompt construction still cost time.

**Fix:** Maintain a `.processed_events.json` manifest per entity type. Skip files already processed unless `force_reprocess: true`.

### 5. Single DynamoDB Read for Cache — 50% fewer reads

Current pattern: `__contains__` (GetItem) + `__getitem__` (GetItem) = 2 reads per cache hit.

**Fix:** Replace with single `get()` method returning `None` on miss.

---

## Already Well-Optimized

| Area | Pattern | Savings vs naive |
|------|---------|-----------------|
| NAT Gateway | Dynamic create/delete via Lambda | ~$32/month |
| Fargate Spot | 4:1 Spot/on-demand weight | Up to 70% compute |
| Batch API | Enabled for Phase 2 + 3 | 50% API cost |
| OpenSERP | Scales to 0 when idle | ~$15/month |
| S3 lifecycle | IA at 30d, Glacier IR at 90d | Storage costs |
| Incremental processing | Only new/changed files | API + compute |
| Preflight check | Cheapest model, 1 token | Prevents wasted runs |

---

## Priority Order

| # | Change | Savings | Effort |
|---|--------|---------|--------|
| 1 | Model tiering | 15-25% Grok cost | Medium |
| 2 | Dynamic SecretsManager endpoint | $14.60/month | Low |
| 3 | Downgrade Container Insights | $5-10/month | Trivial |
| 4 | Increase rate limit to 60/min | Faster runs | Trivial |
| 5 | S3 version expiration | $1-5/month | Trivial |
| 6 | Reduce prompt fulltext | 10-15% tokens | Low |
| 7 | Track processed events | Runtime reduction | Low |
| 8 | Lambda memory reduction | ~$1/month | Trivial |
| 9 | Lambda log retention | $1-3/month | Low |
| 10 | Single DynamoDB cache read | DynamoDB cost | Low |
