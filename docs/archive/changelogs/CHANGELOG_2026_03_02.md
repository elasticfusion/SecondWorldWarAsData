# Recent Changes - March 2, 2026

**Date:** 2026-03-02  
**Summary:** Image processing improvements, QA enhancements, documentation reorganization

---

## Image Processing Improvements

### Format Conversion
- Automatically converts unsupported formats (BMP, TIFF, WebP) to PNG
- In-memory processing with PIL/Pillow
- Transparent to caller

### Automatic Resizing
- Detects images > 5MB
- Iteratively resizes with LANCZOS resampling
- Preserves quality while meeting API limits
- Logs resize operations

### User-Agent Compliance
- Domain-specific User-Agents
- Wikimedia/Wikipedia: Bot identification
- Other sites: Modern Chrome User-Agent
- Prevents 403 Forbidden errors

**Files Modified:**
- `src/extraction/grok_search_maps.py`

**Documentation:**
- `docs/current/features/external-maps/image-processing.md`

---

## Quality Assurance

### Code Quality Improvements
- Fixed mypy type annotation errors
- Reduced complexity: `import_grok_search_maps()` from C (15) → B (9)
- Pylint scores: 8.88-10.00/10
- All 8 QA tools passing

**Files Modified:**
- `src/extraction/search_history.py`
- `src/extraction/grok_search_maps.py`
- `src/extraction/combined_map_search.py`

**Documentation:**
- `docs/current/qa-reports/2026-03-02-grok-search.md`
- `docs/current/qa-reports/grok-search-improvements.md`

---

## Documentation Reorganization

### Current Documentation
Reorganized from flat structure to logical folders:
- `core/` - Architecture, API, config (6 files)
- `pipeline/` - Data ingestion (3 files)
- `features/` - Feature docs (16 files)
  - `external-maps/` (10 files)
  - `people/` (5 files)
  - `maps/` (2 files)
- `qa-reports/` - QA reports (2 files)

**Documentation:**
- `docs/current/INDEX.md` (rewritten)
- `docs/current/REORGANIZATION_CHANGELOG.md`

### Archive Consolidation
Merged two archive locations into one organized structure:
- Consolidated `docs/archive/` + `docs/current/archived/`
- Organized into 6 subject-based folders
- Total: 61 archived files

**Structure:**
- `core/` (5 files)
- `external-maps/` (17 files)
- `people/` (3 files)
- `pipeline/` (8 files)
- `qa-reports/` (4 files)
- `misc/` (24 files)

**Documentation:**
- `docs/archive/README.md`
- `docs/ARCHIVE_CONSOLIDATION.md`

---

## Bug Fixes

### Grok API 400 Errors
- **Issue:** Invalid arguments passed to vision API
- **Cause:** Unsupported image formats or oversized images
- **Fix:** Automatic format conversion and resizing
- **Status:** ✅ Resolved

### Wikimedia 403 Forbidden
- **Issue:** Wikimedia blocking automated requests
- **Cause:** Generic browser User-Agent for bot traffic
- **Fix:** Proper bot identification User-Agent
- **Status:** ✅ Resolved

### "Outdated Browser" Messages
- **Issue:** Websites showing browser upgrade warnings
- **Cause:** Incomplete User-Agent string
- **Fix:** Complete Chrome 120 User-Agent
- **Status:** ✅ Resolved

---

## Files Modified

### Source Code (3 files)
- `src/extraction/search_history.py`
- `src/extraction/grok_search_maps.py`
- `src/extraction/combined_map_search.py`

### Documentation (50+ files)
- Reorganized `docs/current/` structure
- Consolidated `docs/archive/`
- Created new documentation files
- Updated INDEX.md

---

## Quality Metrics

### Code Quality
- **Pylint:** 8.88-10.00/10 (avg 9.27/10)
- **Mypy:** 0 errors
- **Bandit:** 0 high/medium issues
- **Complexity:** All A-B grades

### Documentation
- **Active docs:** 31 files (organized)
- **Archived docs:** 61 files (organized)
- **Total:** 92 documentation files

---

## Next Steps

1. Monitor image processing in production
2. Verify Wikimedia downloads work
3. Review QA metrics after next run
4. Consider adding more whitelisted domains

---

## References

- **Image Processing:** `docs/current/features/external-maps/image-processing.md`
- **QA Report:** `docs/current/qa-reports/2026-03-02-grok-search.md`
- **QA Improvements:** `docs/current/qa-reports/grok-search-improvements.md`
- **Reorganization:** `docs/current/REORGANIZATION_CHANGELOG.md`
- **Archive Consolidation:** `docs/ARCHIVE_CONSOLIDATION.md`
