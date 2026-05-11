# TODO & Known Issues

**Last Updated:** 2026-05-11

---

## Active

### OpenSERP web search from AWS
**Priority:** — | **Status:** Won't Do

Search engines block AWS datacenter IPs. Rate limiting and fallback engines don't resolve the fundamental issue. OpenSERP web search requires residential IPs or a paid SERP API. Pipeline enrichment works without it (Grok bio, NARA, Archive.org all functional).

### Batch mode for optional extractors
**Priority:** Medium | **Type:** Enhancement

Optional extractors (casualties, weather, equipment, logistics, supplemental) use real-time API calls even in `--batch` mode. Fix: add second batch collect→submit→poll cycle.

### Re-extract entities with updated prompts
**Priority:** Medium | **Type:** Enhancement

After cache clear, re-run with prompts requesting: places `original_text`/`role_in_event`, people `position_at_event`, groups `context`.

---

## Known Issues

### Search engines block AWS datacenter IPs
**Severity:** High

Google/Bing/DuckDuckGo return 503 or empty from Fargate. Options: residential proxy, or accept rate-limited Bing/DuckDuckGo only.

### Military units misclassified as places
**Severity:** Medium

Phase 2 sometimes puts military units in `output/places/`. Auto-reclassify script and dedup UI reclassify button available.

### 548 stub files missing primary IDs
**Severity:** Low

107 people, 228 places, 183 groups, 29 dates have only `event_mentions` — no ID field. Should be cleaned up or regenerated.

### NARA Catalog API returning HTML
**Severity:** Low

NARA API returns HTML instead of JSON. Reported to Catalog_API@nara.gov. Grok Record Group identification works as fallback.

### Grok fast model appends commentary after JSON
**Severity:** Low

Retry logic and JSON repair handle most cases.

---

## Backlog

### Periodic re-search of not_found entities
Add config `enrichment.re_search_after_days: 90` to retry entities after threshold.

### True batch submission for OpenSERP verification
Collect candidates → submit as Grok batch → apply results.

### Configurable OpenSERP search depth
Config: `results_per_query`, `max_images_per_entity`, `max_web_results_per_entity`, `rate_limit_seconds`.

### Unmatched combinable people files
Dedup missed: `hitler.json`/`adolf hitler.json`, `eisenhower.json`/`supreme commander.json`, `george patton.json`/`george s. patton, jr..json`.

### Phase 4: Document Acquisition & Processing
Download digitized sources, OCR, feed back through pipeline. Spec: `docs/current/PHASE4_SPEC.md`.

### UK National Archives (Discovery API)
British military records integration.

### ~~Windows PowerShell scripts~~
**Status:** Won't Do

### Move find_duplicates scoring to src/dedup/scoring.py

---

## Completed

### 2026-05-11
- ✅ BatchModeCollecting fix
- ✅ Final sync fix (Phase 3 uploads all files)
- ✅ OpenSERP endpoint fix (`POST /search` → `GET /mega/search`)
- ✅ OpenSERP `serve` command in task definition
- ✅ NAT teardown via SNS (immediate on completion)
- ✅ LOC removed from search chain
- ✅ Schema versioning (all 11 entity types)
- ✅ NOAA weather enrichment module
- ✅ IAM least privilege audit
- ✅ ALB fully removed
- ✅ Port 443 SG rule for ECR pulls
- ✅ Bibliography verbatim fix
- ✅ OpenSERP scales to 0 on completion
- ✅ `openserp_searched` race condition fix

### 2026-05-06
- ✅ NARA Record Group identification via Grok
- ✅ External search cache (positive 30d / negative 7d)
- ✅ `enrichment_status` tracking
- ✅ Idle monitor rewritten

### 2026-05-01 – 2026-05-03
- ✅ Dedup exclusions → DynamoDB
- ✅ Casualties spec rewritten
- ✅ Output directory reorganized
- ✅ NAT Gateway dynamic lifecycle

### Earlier
- ✅ xAI Batch API (50% cost reduction)
- ✅ Event extraction token optimization (~72% reduction)
- ✅ Cross-reference consistency fixes
- ✅ Rate limiter, retry logic, ULID fixing
