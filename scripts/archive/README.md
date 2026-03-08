# Scripts Archive

**Date:** 2026-03-05

This directory contains archived scripts that are no longer actively used but kept for reference.

---

## Directory Structure

### qa/
QA and formatting scripts used during development. One-time use for specific features.

- `qa_concurrent.py` - QA checks for concurrent processing implementation
- `qa_logistics.py` - QA checks for logistics extraction
- `check_black.py` - Black formatter verification
- `format_files.py` - Format specific files

### testing/
Test scripts used during development and debugging.

- `test_grok_api.py` - Test Grok API connection
- `test_place_extraction.py` - Test place extraction
- `test_grok_search.sh` - Test Grok search functionality
- `test_blacklist_comments.sh` - Test domain blacklist
- `qa_check_tests.sh` - Run QA checks
- `run_tests.sh` - Run test suite

### migration/
One-time migration scripts for schema changes and data updates.

- `migrate_people_schema.py` - Migrate people to new schema (completed)
- `migrate_place_schema.py` - Migrate places to new schema (completed)
- `verify_requests_migration.py` - Verify httpx→requests migration (completed)
- `fix_place_map_urls.py` - Fix place map URLs (completed)
- `deduplicate_ranks.py` - Deduplicate military ranks (completed)

### obsolete/
Scripts replaced by newer implementations or no longer needed.

- `merge_duplicate_groups.py` - Replaced by `merge_related_groups.py` (config-driven → interactive)
- `verify_phase2_setup.py` - Setup verification (no longer needed)
- `cleanup_people.sh` - One-time cleanup (completed)
- `enrich_equipment.py` - Enrichment now integrated in extraction
- `verify_and_import.py` - Import verification (obsolete)

---

## Notes

These scripts are kept for:
- Historical reference
- Understanding past implementations
- Potential future reuse of logic

They are not maintained and may not work with current codebase.

For active scripts, see the main `scripts/` directory.
