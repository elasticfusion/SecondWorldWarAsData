ar# TODO

**Last Updated:** 2026-03-19

---

## Active

### Wire `supplemental_advanced.py` to `output/bibliography/`
**Priority:** Medium  
**Status:** Not Started

Phase 3 enrichment (`supplemental_advanced.py`) still reads from old `output/supplemental/` location. Needs to read from `output/bibliography/` and enrich bibliography entries with ISBN, copyright status, and author death dates.

### Fix `output/people_groups/3ad.json`
**Priority:** Low  
**Status:** Not Started

Broken people_group file — only contains `event_mentions` array, missing `GroupID` and `name` fields. Detected by `scripts/validate_output.py`.

---

## Windows PowerShell Scripts

**Priority:** Medium  
**Status:** Not Started

### Objective
Create PowerShell equivalents for all bash shell scripts to support Windows users.

### Shell Scripts Requiring PowerShell Equivalents

#### Active Scripts (8)
1. **tools/setup_openserp.sh** - OpenSERP service setup
2. **scripts/test_grok_search.sh** - Test Grok search functionality
3. **scripts/test_blacklist_comments.sh** - Test domain blacklist
4. **scripts/run_tests.sh** - Run test suite
5. **scripts/archive_merge_duplicate_groups.sh** - Archive merge script
6. **scripts/cleanup_people.sh** - People data cleanup
7. **scripts/archive_scripts.sh** - Archive old scripts
8. **scripts/qa_check_tests.sh** - QA test checker

### Implementation Plan

1. **Create scripts/powershell/ directory**
2. **Port each .sh script to .ps1**
   - Maintain same functionality
   - Use PowerShell idioms
   - Test on Windows 10/11
3. **Update documentation**
   - Add PowerShell examples to scripts/README.md
   - Update main README.md with PowerShell usage
   - Add Windows-specific troubleshooting
4. **Create master script**
   - `scripts/powershell/run_all_tests.ps1`
   - Equivalent to run_tests.sh

### Notes
- PowerShell 5.1+ required (built into Windows 10+)
- Consider cross-platform PowerShell Core (7+)
- Test on both Windows CMD and PowerShell environments

### Related Documentation
- [Scripts Reference](../../scripts/README.md)
- [Development Guide](core/DEVELOPMENT.md)

---

## Future Enhancements

### Documentation
- Add diagrams - Convert ASCII diagrams to images
- Add screenshots - Visual examples of output
- Add tutorials - Step-by-step guides for common workflows
- Generate API docs - Auto-generate from docstrings

### Features
- Review existing feature docs (48 docs) for accuracy against current code
- Add integration tests for full pipeline
- Add performance benchmarks
- Add data quality metrics

---

## Completed

### Supplemental Split Architecture (✅ 2026-03-19)
- Bibliography + factual content split routing
- `alt_title` field for expanded abbreviations
- ULID fixing wired into all extractors
- JSON schema validation for casualties and people_groups
- Comprehensive output validation script (`scripts/validate_output.py`)

### Resolved TODOs (Archived 2026-03-19)
- `TODO_supplemental_naming_collision.md` — Fixed by book-prefixed filenames
- `TODO_per_book_cache.md` — Implemented per-book cache routing
- `TODO_short_response_rejection.md` — Fixed short valid JSON acceptance

### Phase 1-5: Comprehensive Documentation (✅ 2026-03-13)
- Created 7 comprehensive feature READMEs
- Updated core pipeline documentation
- Created scripts and tools documentation
- Archived outdated documents
- Cleaned up project structure
