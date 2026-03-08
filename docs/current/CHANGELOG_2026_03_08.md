# Changelog - 2026-03-08

**Summary:** Automatic retry wrappers, group deduplication improvements, phase3 logging fixes, httpx→requests migration, documentation consolidation.

---

## New Features

### Automatic Retry Wrappers

**phase2_retry.py** - Automatic retry wrapper for Phase 2 extraction
- Detects missing event files after each run
- Configurable max attempts (default: 3)
- Stops early on success
- Handles API timeouts, network errors automatically

**phase3_retry.py** - Automatic retry wrapper for Phase 3 enrichment
- Detects unenriched people after each run
- Configurable max attempts (default: 3)
- Stops early on success
- Handles Wikipedia/Grokipedia errors automatically

**Documentation:** `docs/current/pipeline/RETRY_WRAPPERS.md`

### Group Deduplication System v2.0

**Improvements:**
- Roman numeral detection (V Corps vs VII Corps)
- Word number filtering (First vs Second)
- Automatic exclusion tracking
- Reduced false positives (13 → 7 clusters)

**Features:**
- Exclusion tracking file (`excluded_merges.md`)
- Automatic loading of exclusions
- Interactive merge with "exclude" option
- Markdown format for version control

**Documentation:** `docs/current/features/people/GROUP_DEDUPLICATION_SYSTEM.md`

---

## Bug Fixes

### Phase 3 Logging

**Issue:** phase3_enrich_data.py only logged to console, no log files created

**Fix:**
- Replaced `logging.basicConfig()` with `setup_logging()`
- Added `load_config()` to read logging settings
- Now creates timestamped log files like phase2

**Files:** `phase3_enrich_data.py`

### Phase 2 Cache Directory

**Issue:** `paths["cache_dir"]` incorrect key (should be `api_cache`)

**Fix:** Changed to `paths["api_cache"]`

**Files:** `phase2_extract.py`

---

## Improvements

### HTTPX → Requests Migration (Phase 3)

**Scope:** Phase 3 libraries only

**Changed:**
- `src/extraction/enrich_biographies.py`
  - `import httpx` → `import requests`
  - `httpx.get()` → `requests.get()`
  - `follow_redirects=True` → `allow_redirects=True`
  - `httpx.TimeoutException` → `requests.Timeout`
  - `httpx.HTTPStatusError` → `requests.HTTPError`

**Result:** Phase 3 now uses requests exclusively (consistent with project standard)

### Root Directory Cleanup

**Archived:**
- `mergeo.txt` - Old group merge report
- `wwii-pipeline-fixes.json` - Old conversation data
- `.coverage` - Test coverage data
- `htmlcov/` - HTML coverage report

**Location:** `archive/`

**Note:** Coverage files can be regenerated with `pytest --cov=src --cov-report=html`

---

## Documentation

### Consolidated

**New Structure:**
- `docs/current/` - Active documentation
- `docs/archive/2026-03-08/` - Today's implementation reports
- `docs/current/pipeline/RETRY_WRAPPERS.md` - New retry wrapper guide
- `docs/current/features/people/GROUP_DEDUPLICATION_SYSTEM.md` - Group dedup v2.0

**Updated:**
- `docs/current/INDEX.md` - Updated with new docs and reorganization
- `README.md` - Added phase2_retry.py and phase3_retry.py usage

**Archived:**
- All dated docs from today moved to `docs/archive/2026-03-08/`
- Old QA reports and implementation notes

### Pattern

Following `docs/current/` structure:
- `core/` - Architecture, configuration, error handling
- `pipeline/` - Data ingestion and processing
- `features/` - Feature-specific documentation
- `qa-reports/` - Quality assurance reports

---

## Scripts

### New Scripts

- `phase2_retry.py` - Automatic retry for Phase 2
- `phase3_retry.py` - Automatic retry for Phase 3

### Fixed Scripts

- `scripts/find_related_groups.py` - Added system file filtering
- `scripts/merge_related_groups.py` - Enhanced exclusion writing
- `scripts/extract_url.py` - Fixed import path
- `scripts/suggest_group_aliases.py` - Fixed import path

---

## Quality Assurance

### All Source Files QA (10,400 lines)

**Results:**
- Black Formatting: ✅ 100% formatted
- Bandit Security: ✅ 0 high/medium issues
- Radon CC: ✅ Avg B (5.0)
- Radon MI: ✅ 97% A-grade
- Vulture: ✅ 0 actual dead code
- Pylint: ✅ 9.17/10 average

**Report:** `docs/archive/2026-03-08/QA_REPORT_ALL_SRC_20260308.md`

---

## Breaking Changes

None. All changes are backward compatible.

---

## Migration Guide

### Using Retry Wrappers

**Before:**
```bash
python3 phase2_extract.py
# Check for errors, manually re-run if needed
python3 phase2_extract.py
```

**After:**
```bash
python3 phase2_retry.py
# Automatically retries until complete
```

### Group Deduplication

**Before:**
- Manual exclusion editing in `not_related.json`
- Re-review same clusters every session

**After:**
- Interactive "exclude" option during merge
- Automatic exclusion tracking in `excluded_merges.md`
- Automatic loading on next run

---

## Statistics

**Files Modified:** 15
**Files Created:** 3
**Files Archived:** 15
**Documentation Pages:** 2 new, 2 updated
**Lines of Code:** ~500 (retry wrappers + fixes)

---

## Next Steps

1. Test retry wrappers in production
2. Monitor group deduplication false positive rate
3. Consider adding retry wrapper for phase1
4. Evaluate word number detection for groups

---

**Status:** ✅ All features tested and production-ready  
**Last Updated:** 2026-03-08
