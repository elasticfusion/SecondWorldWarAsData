# QA Gaps & Improvement Recommendations

**Date:** 2026-05-24  
**Scope:** Full codebase review — test coverage, error handling, validation, CI/CD

---

## Executive Summary

The project has solid foundations in specific areas (Grok API retry logic, atomic file writes, DynamoDB mocking) but significant gaps in overall test coverage (~25% module coverage, ~15% line coverage), configuration validation, and CI enforcement. The most critical risks are untested orchestration code, silent failure modes, and a CI pipeline that cannot block broken merges.

---

## 1. Test Coverage Gaps

### 1.1 Critical Untested Modules

| Module | Lines | Risk | Why It Matters |
|--------|-------|------|----------------|
| `src/extraction/batch_parallel.py` | 1007 | CRITICAL | Orchestrates all parallel extraction — untested concurrency, locking, error isolation |
| `src/extraction/equipment.py` | 1959 | CRITICAL | Largest module in codebase — only image hash function has tests |
| `lambda_handlers/dedup_ui_handler.py` | 1141 | CRITICAL | User-facing Lambda, handles merges/reclassification — zero tests |
| `src/extraction/enrich_biographies.py` | 840 | HIGH | External API enrichment with no mocking |
| `src/extraction/weather_central.py` | 793 | HIGH | Open-Meteo integration, no tests |
| `src/extraction/maps.py` | 793 | HIGH | Map extraction logic, no tests |
| `src/extraction/logistics.py` | 698 | HIGH | Logistics extraction, no tests |
| `src/extraction/casualties.py` | 623 | HIGH | Casualty extraction, no tests |
| `src/enrichment/bibliography_resolver.py` | 645 | HIGH | External search integration, no tests |
| `src/dedup/exclusions.py` | 363 | HIGH | Core dedup logic, no tests |
| `phase3_enrich_data.py` | — | HIGH | Entire enrichment phase untested |

### 1.2 Anti-Pattern Tests (Provide False Confidence)

These test files exist but have **zero assertions** — they print output and require manual inspection:

- `tests/test_supplemental.py` — 1 function, no asserts, requires live API
- `tests/test_supplemental_complete.py` — 3 functions, no asserts, requires live API
- `tests/test_equipment_deduplication.py` — 2 functions, no asserts, uses `sys.exit()`, requires real images

**Recommendation:** Convert to proper pytest tests with assertions and mocked dependencies, or delete them and replace with real tests.

### 1.3 Missing External Service Mocking

| Service | Mocked? | Affected Modules |
|---------|---------|------------------|
| S3 | ❌ | `storage.py`, `s3_lazy.py`, `ecs_entrypoint.py` |
| OpenSERP | ❌ | `openserp_client.py`, `openserp_enrichment.py`, `openserp_maps.py` |
| NOAA Weather API | ❌ | `noaa_weather.py` |
| Open-Meteo API | ❌ | `weather_central.py` |
| MongoDB | ❌ | `import_to_mongodb.py` |
| Wikipedia/Grokipedia | ❌ | `enrich_biographies.py` |

**Recommendation:** Use `moto` for S3 (already used for DynamoDB), `responses` or `respx` for HTTP APIs.

---

## 2. Error Handling Gaps

### 2.1 Good Patterns (Keep These)

- Layered retry with backoff in `grok_client.py` (tenacity + rate limiter + wall-clock deadline)
- Error isolation in parallel processing (`asyncio.gather(return_exceptions=True)`)
- Atomic file writes via `tempfile.mkstemp` + `os.replace` in `write_json_with_lock`
- DynamoDB conditional writes for idempotent job claiming in `batch_poller.py`
- Auto-healing cache (poisoned/truncated entries detected and purged)
- Preflight credit check before expensive processing in `ecs_entrypoint.py`

### 2.2 Critical Gaps

| Issue | Location | Impact |
|-------|----------|--------|
| No SIGTERM handler | `ecs_entrypoint.py` | ECS kills container without final S3 sync — data loss |
| No circuit breaker | `grok_client.py` | Persistent API failures cause repeated slow retries instead of fast-fail |
| Non-atomic writes | `src/utils/storage.py` `LocalStorage.write_json` | Uses `write_text()` directly — crash mid-write corrupts file |
| No task-level timeouts | `batch_parallel.py` | Hung async tasks block entire batch indefinitely |
| Silent exception swallowing | Multiple locations | `except Exception: pass` hides errors (e.g., `_clear_manifest`, `_clear_all_locks`) |
| No disk space checks | `ecs_entrypoint.py`, `cache_backend.py` | Unbounded cache growth, no pre-write verification |
| Non-idempotent merges | `dedup_ui_handler.py` | Lambda retry could duplicate event_mentions |
| ConnectionError not retried | `grok_client.py` | tenacity only retries `HTTPError`, not `ConnectionError` |
| No Lambda timeout awareness | `dedup_ui_handler.py` | Complex merges could exceed Lambda timeout |
| Index/entity inconsistency | `batch_parallel.py` | Index updated before entity file write confirmed |

### 2.3 Recommendations

1. **Add SIGTERM handler** to `ecs_entrypoint.py` — trigger final S3 sync on signal
2. **Implement circuit breaker** — after N consecutive failures, fast-fail for a cooldown period
3. **Make all writes atomic** — replace `LocalStorage.write_json` with `write_json_with_lock` pattern
4. **Add per-task timeouts** — `asyncio.wait_for(task, timeout=300)` in batch_parallel
5. **Replace silent swallowing** — at minimum log at WARNING level with context
6. **Add `ConnectionError` to retry filter** in grok_client tenacity decorator

---

## 3. Configuration & Validation Gaps

### 3.1 Zero Config Validation

`src/utils/config.py` loads YAML with no validation:
- No required field checks
- No type enforcement
- No range validation
- No defaults for missing values
- No AWS-mode field verification

**Impact:** Typos in config keys silently ignored. Invalid values (e.g., `timeout: "thirty"`) only fail at runtime deep in the pipeline.

**Recommendation:** Add a config schema (Pydantic model or JSON Schema) validated at load time. Fail fast with clear error messages.

### 3.2 Schema Inconsistencies

| Issue | Detail |
|-------|--------|
| Dual versioning | `json_schemas.py` uses "1.0.0", output schemas use "2.3" — no clear relationship |
| Field name mismatch | Weather extraction uses `WeatherMentionID`, output uses `WeatherID` |
| Permissiveness drift | Logistics output schema is MORE permissive than extraction schema |
| No `additionalProperties: false` | Extraction schemas allow LLM-hallucinated fields to pass undetected |
| Minimal schemas | People, Groups, Equipment extraction schemas only require ID + name |
| No `minLength` | Empty strings pass all schema validation |

**Recommendation:** Unify schema versioning. Add `additionalProperties: false` to extraction schemas. Add `minLength: 1` to required string fields.

### 3.3 Missing Validation

- **No markdown input validation** — malformed input silently produces empty output
- **No coordinate range validation** — lat/lon values unchecked
- **No WWII date range validation** — future dates pass (e.g., "2099-01-01")
- **No cross-reference integrity** — nested ULIDs (inside arrays) not validated
- **No dedup report structure validation** — assumes correct format

---

## 4. CI/CD Gaps

### 4.1 Current State

The GitHub Actions workflow (`validation.yml`):
- Only validates **3 of 11 entity types** (People, Equipment, Events)
- Uses `continue-on-error: true` on all steps — **failures never block merges**
- Runs **no unit tests** — only data validation
- Runs **no linting** — pre-commit hooks are local-only
- Uses **Python 3.11** while development uses 3.12
- Has **no dependency pinning** — `pip install jsonschema pytest` installs latest

### 4.2 Pre-commit Hooks

- Has a **hardcoded macOS path** (`/Users/dchristian/...`) — won't work on Linux/CI
- No security scanning (bandit, pip-audit)
- No type checking (mypy)
- No secrets detection (detect-secrets)
- No YAML validation for config changes
- Pylint disables broad-exception warnings — masks the silent swallowing problem

### 4.3 Recommendations

1. **Remove `continue-on-error: true`** — or at minimum add a final step that fails if any validation failed
2. **Add unit tests to CI** — `pytest tests/ -m "not slow"` should run on every PR
3. **Validate all 11 entity types** — not just 3
4. **Pin Python version** to 3.12 to match development
5. **Pin dependencies** with exact versions or a lockfile
6. **Add linting to CI** — run black/pylint/mypy checks
7. **Fix hardcoded path** in `.pre-commit-config.yaml`
8. **Add secrets scanning** — `detect-secrets` or `gitleaks`

---

## 5. Data Integrity Risks

| Risk | Detail | Mitigation |
|------|--------|------------|
| Silent ULID replacement | Invalid ULIDs auto-replaced with new ones — breaks cross-references | Log replacements as warnings; validate referential integrity after |
| Aggressive JSON sanitization | Adding closing braces to malformed LLM output can create valid but semantically wrong JSON | Add semantic validation after structural repair |
| Unbounded cache growth | `DiskCacheBackend` has no size limits | Add max-size with LRU eviction |
| Non-paginated DynamoDB clear | `DynamoCacheBackend.clear()` only processes first page | Add pagination loop |
| Orphaned output files | Changing `paths.output_root` without migration orphans data | Add config change detection/migration |
| No backup before merge | Dedup merges delete secondary files with no undo | Write backup before destructive operations |

---

## 6. Priority Action Plan

### Immediate (Week 1-2)

1. **Fix anti-pattern tests** — convert or replace the 3 assertion-less test files
2. **Add SIGTERM handler** to `ecs_entrypoint.py`
3. **Remove `continue-on-error: true`** from CI validation steps
4. **Add unit tests to CI** workflow
5. **Make `LocalStorage.write_json` atomic** (use tempfile + os.replace)

### Short-term (Month 1)

6. **Add config validation** — Pydantic model or JSON Schema for `config.yaml`
7. **Write tests for `batch_parallel.py`** — mock Grok API, test error isolation and concurrency
8. **Write tests for `dedup_ui_handler.py`** — mock DynamoDB/S3, test merge idempotency
9. **Add S3 mocking** with moto for storage layer tests
10. **Add `ConnectionError` to retry filter** in grok_client
11. **Fix pre-commit hardcoded path**
12. **Unify schema versioning** — single version number across extraction and output

### Medium-term (Month 2-3)

13. **Add tests for all extraction modules** — at minimum happy-path + error-path per module
14. **Implement circuit breaker** for Grok API
15. **Add per-task timeouts** in batch_parallel async processing
16. **Add security scanning** to CI (bandit, detect-secrets)
17. **Add type checking** (mypy) to CI
18. **Validate all 11 entity types** in CI
19. **Add integration tests** for phase3 enrichment pipeline
20. **Add Lambda handler tests** with moto for remaining 7 handlers

### Long-term (Quarter)

21. **Achieve 60%+ line coverage** across src/
22. **Add end-to-end pipeline test** (parse → extract → enrich → import with all services mocked)
23. **Add performance regression tests** — track extraction time per chapter
24. **Add data quality monitoring** — automated checks for schema drift, empty arrays, broken cross-references
25. **Implement structured error reporting** — machine-readable failure summaries per pipeline run

---

## Appendix: Module Coverage Matrix

```
✅ = Good tests    ⚠️ = Partial/weak    ❌ = No tests

src/grok_client.py              ⚠️  (5 tests, misses retry/rate-limit/streaming)
src/parser.py                   ❌
src/json_schemas.py             ✅
src/extraction/batch_parallel   ❌  ← CRITICAL
src/extraction/people           ⚠️  (normalization only)
src/extraction/equipment        ❌  ← CRITICAL (1959 lines)
src/extraction/supplemental     ❌  (anti-pattern tests)
src/extraction/events           ❌
src/extraction/dates            ❌
src/extraction/places           ❌
src/extraction/logistics        ❌
src/extraction/casualties       ❌
src/extraction/weather_central  ❌
src/extraction/maps             ❌
src/extraction/enrich_*         ❌
src/utils/json_validator        ✅
src/utils/text_utils            ✅
src/utils/custom_validators     ✅
src/utils/batch_api             ✅
src/utils/job_queue             ✅
src/utils/config                ❌
src/utils/storage               ❌
src/utils/entity_index          ❌
src/utils/cache_backend         ❌
src/dedup/exclusions            ❌
src/dedup/merge                 ❌
src/enrichment/*                ❌
lambda_handlers/batch_poller    ✅
lambda_handlers/dedup_ui        ❌  ← CRITICAL
lambda_handlers/* (7 others)    ❌
phase1_parse                    ⚠️  (2 helpers only)
phase2_extract                  ⚠️  (integration only)
phase3_enrich_data              ❌
import_to_mongodb               ❌
import_to_dynamodb              ❌
ecs_entrypoint                  ⚠️  (routing only)
```
