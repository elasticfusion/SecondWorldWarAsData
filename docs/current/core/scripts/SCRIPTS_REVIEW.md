# Scripts Review and Archive Plan

**Date:** 2026-03-05

---

## Active Scripts (Keep)

### Deduplication & Merging
- ✅ `find_duplicate_people.py` - Find duplicate person records
- ✅ `merge_duplicate_people.py` - Merge duplicate people
- ✅ `find_duplicate_places.py` - Find duplicate places
- ✅ `merge_duplicate_places.py` - Merge duplicate places
- ✅ `find_related_groups.py` - Find related people groups
- ✅ `merge_related_groups.py` - Merge related groups
- ✅ `suggest_group_aliases.py` - Suggest group name aliases
- ✅ `consolidate_people_groups.py` - Consolidate groups with aliases
- ✅ `merge_equipment.py` - Merge equipment records

### Metadata Management
- ✅ `complete_metadata_with_grok.py` - Complete missing metadata
- ✅ `generate_missing_metadata.py` - Generate metadata files
- ✅ `standardize_metadata.py` - Standardize metadata format

### Utilities
- ✅ `review_cache.py` - Review API cache
- ✅ `validate_places.py` - Validate place data
- ✅ `extract_url.py` - Extract URLs from data

---

## Temporary/Testing Scripts (Archive)

### QA Scripts (Temporary)
- 🗄️ `qa_concurrent.py` - QA for concurrent changes (one-time use)
- 🗄️ `qa_logistics.py` - QA for logistics (one-time use)
- 🗄️ `check_black.py` - Black formatter check (one-time use)
- 🗄️ `format_files.py` - Format specific files (one-time use)

### Test Scripts
- 🗄️ `test_grok_api.py` - Test Grok API connection
- 🗄️ `test_place_extraction.py` - Test place extraction
- 🗄️ `test_grok_search.sh` - Test Grok search
- 🗄️ `test_blacklist_comments.sh` - Test blacklist
- 🗄️ `qa_check_tests.sh` - QA check tests
- 🗄️ `run_tests.sh` - Run test suite

### Migration Scripts (One-time)
- 🗄️ `migrate_people_schema.py` - Migrate people schema (completed)
- 🗄️ `migrate_place_schema.py` - Migrate place schema (completed)
- 🗄️ `verify_requests_migration.py` - Verify httpx→requests (completed)
- 🗄️ `fix_place_map_urls.py` - Fix place map URLs (one-time)
- 🗄️ `deduplicate_ranks.py` - Deduplicate ranks (one-time)

### Obsolete Scripts
- 🗄️ `merge_duplicate_groups.py` - Replaced by merge_related_groups.py
- 🗄️ `verify_phase2_setup.py` - Setup verification (obsolete)
- 🗄️ `cleanup_people.sh` - One-time cleanup
- 🗄️ `enrich_equipment.py` - Enrichment now in extraction
- 🗄️ `verify_and_import.py` - Import verification (obsolete)

### Content Processing (Keep if used)
- ⚠️ `pdf_to_markdown.py` - Convert PDFs (keep if needed)
- ⚠️ `split_chapters.py` - Split chapters (keep if needed)

---

## Archive Structure

```
scripts/
├── archive/
│   ├── qa/
│   │   ├── qa_concurrent.py
│   │   ├── qa_logistics.py
│   │   ├── check_black.py
│   │   └── format_files.py
│   ├── testing/
│   │   ├── test_grok_api.py
│   │   ├── test_place_extraction.py
│   │   ├── test_grok_search.sh
│   │   ├── test_blacklist_comments.sh
│   │   ├── qa_check_tests.sh
│   │   └── run_tests.sh
│   ├── migration/
│   │   ├── migrate_people_schema.py
│   │   ├── migrate_place_schema.py
│   │   ├── verify_requests_migration.py
│   │   ├── fix_place_map_urls.py
│   │   └── deduplicate_ranks.py
│   └── obsolete/
│       ├── merge_duplicate_groups.py
│       ├── verify_phase2_setup.py
│       ├── cleanup_people.sh
│       ├── enrich_equipment.py
│       └── verify_and_import.py
├── find_duplicate_people.py
├── merge_duplicate_people.py
├── find_duplicate_places.py
├── merge_duplicate_places.py
├── find_related_groups.py
├── merge_related_groups.py
├── suggest_group_aliases.py
├── consolidate_people_groups.py
├── merge_equipment.py
├── complete_metadata_with_grok.py
├── generate_missing_metadata.py
├── standardize_metadata.py
├── review_cache.py
├── validate_places.py
├── extract_url.py
├── pdf_to_markdown.py (if needed)
└── split_chapters.py (if needed)
```

---

## Summary

**Keep:** 15 active scripts
**Archive:** 20 temporary/obsolete scripts

**Categories:**
- QA scripts: 4 (temporary, one-time use)
- Test scripts: 6 (development testing)
- Migration scripts: 5 (one-time migrations)
- Obsolete scripts: 5 (replaced or no longer needed)
