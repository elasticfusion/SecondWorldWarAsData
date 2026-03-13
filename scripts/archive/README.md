# Archived Scripts

**Date:** 2026-03-13

---

## Overview

This directory contains scripts that are no longer actively used but are preserved for historical reference.

---

## Categories

### QA Scripts (qa/)
One-time quality assurance scripts used during development.

- `qa_concurrent.py` - QA for concurrent processing changes
- `qa_logistics.py` - QA for logistics extraction
- `check_black.py` - Black code formatter check
- `format_files.py` - Format specific files

**Status:** Completed, no longer needed

---

### Migration Scripts (migration/)
One-time schema migration scripts.

- `migrate_people_schema.py` - Migrated people schema (completed 2026-03-08)
- `migrate_place_schema.py` - Migrated place schema (completed 2026-03-08)
- `migrate_schema.py` - General schema migration tool (completed)
- `verify_requests_migration.py` - Verified httpx→requests migration (completed 2026-03-08)
- `deduplicate_ranks.py` - Deduplicated rank values (completed)
- `fix_place_map_urls.py` - Fixed place map URLs (completed)

**Status:** Migrations completed, kept for reference

---

### Testing Scripts (testing/)
Development testing scripts.

- `test_grok_api.py` - Test Grok API connection
- `test_place_extraction.py` - Test place extraction manually
- `verify_phase2_setup.py` - Verify Phase 2 setup

**Status:** Replaced by formal test suite in `tests/`

---

### Obsolete Scripts
Scripts replaced by better implementations.

- `verify_and_import.py` - Import verification (replaced by import_to_mongodb.py)
- `enrich_equipment.py` - Equipment enrichment (now integrated into extraction pipeline)

**Status:** Functionality moved to main pipeline

---

## Why Archived?

Scripts are archived when:
1. **One-time use completed** - Migrations, QA checks
2. **Replaced by better implementation** - Integrated into pipeline
3. **No longer relevant** - Feature removed or changed
4. **Development only** - Ad-hoc testing scripts

---

## Restoration

If you need to restore an archived script:

```bash
# Copy back to scripts/
cp scripts/archive/qa/qa_concurrent.py scripts/

# Or reference directly
python3 scripts/archive/migration/migrate_people_schema.py
```

---

## Related Documentation

- [Active Scripts](../README.md)
- [Pipeline Documentation](../../docs/current/core/PIPELINE.md)
