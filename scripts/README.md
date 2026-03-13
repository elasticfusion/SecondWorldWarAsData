# Scripts Reference

**Last Updated:** 2026-03-13

---

## Overview

Utility scripts for data management, validation, and maintenance tasks. All scripts are located in the `scripts/` directory.

---

## Categories

### Deduplication & Merging
- [find_duplicate_people.py](#find_duplicate_peoplepy)
- [merge_duplicate_people.py](#merge_duplicate_peoplepy)
- [find_duplicate_places.py](#find_duplicate_placespy)
- [merge_duplicate_places.py](#merge_duplicate_placespy)
- [find_related_groups.py](#find_related_groupspy)
- [merge_related_groups.py](#merge_related_groupspy)
- [merge_equipment.py](#merge_equipmentpy)
- [consolidate_people_groups.py](#consolidate_people_groupspy)
- [suggest_group_aliases.py](#suggest_group_aliasespy)

### Metadata Management
- [complete_metadata_with_grok.py](#complete_metadata_with_grokpy)
- [generate_missing_metadata.py](#generate_missing_metadatapy)
- [standardize_metadata.py](#standardize_metadatapy)

### Content Processing
- [pdf_to_markdown.py](#pdf_to_markdownpy)
- [split_chapters.py](#split_chapterspy)
- [process_supplemental_info.py](#process_supplemental_infopy)

### Validation
- [validate_data.py](#validate_datapy)
- [validate_places.py](#validate_placespy)
- [validate_supplemental_urls.py](#validate_supplemental_urlspy)
- [validation_report.py](#validation_reportpy)

### Utilities
- [review_cache.py](#review_cachepy)
- [extract_url.py](#extract_urlpy)
- [generate_dashboard.py](#generate_dashboardpy)
- [generate_schema_docs.py](#generate_schema_docspy)
- [generate_type_stubs.py](#generate_type_stubspy)

---

## Deduplication & Merging

### find_duplicate_people.py

Find potential duplicate person records using multiple heuristics.

**Usage:**
```bash
python3 scripts/find_duplicate_people.py
```

**Output:** `output/people/duplicate_report.json`

**Detection Methods:**
- Name similarity (70%+ threshold)
- ASCII/Unicode variants (Dönitz ↔ Donitz)
- Substring matching (Eisenhower ↔ D. Eisenhower)
- Shared biographical data

**Next Step:** Review report and run `merge_duplicate_people.py`

---

### merge_duplicate_people.py

Interactively merge duplicate person records.

**Usage:**
```bash
python3 scripts/merge_duplicate_people.py
```

**Requires:** `output/people/duplicate_report.json` (from find_duplicate_people.py)

**Interactive Prompts:**
- `y` - Merge the group
- `n` - Stop processing
- `skip` - Skip this group, continue to next
- `exclude` - Add to exclusion list (not duplicates)

**Features:**
- Selective exclusion (exclude specific items)
- Choose primary record
- Merge event mentions
- Merge alternate names
- Saves exclusions to `output/people/not_duplicates.json`

---

### find_duplicate_places.py

Find potential duplicate place records.

**Usage:**
```bash
python3 scripts/find_duplicate_places.py
```

**Output:** `output/places/duplicate_report.json`

**Detection Methods:**
- Name similarity
- Coordinate proximity
- Alternate name matching

---

### merge_duplicate_places.py

Interactively merge duplicate place records.

**Usage:**
```bash
python3 scripts/merge_duplicate_places.py
```

**Requires:** `output/places/duplicate_report.json`

**Same interactive workflow as merge_duplicate_people.py**

---

### find_related_groups.py

Find related people groups (military units/organizations).

**Usage:**
```bash
python3 scripts/find_related_groups.py
```

**Output:** `output/people_groups/related_groups_report.json`

**Detection Methods:**
- Name similarity
- Hierarchical relationships (parent/child units)
- Roman numeral detection (V Corps vs VII Corps)
- Word number filtering (First vs Second)

---

### merge_related_groups.py

Interactively merge related group records.

**Usage:**
```bash
python3 scripts/merge_related_groups.py
```

**Requires:** `output/people_groups/related_groups_report.json`

**Same interactive workflow as merge_duplicate_people.py**

---

### merge_equipment.py

Merge duplicate equipment records.

**Usage:**
```bash
python3 scripts/merge_equipment.py
```

**Features:**
- Finds equipment with similar names
- Merges specifications
- Merges media references
- Preserves all mentions

---

### consolidate_people_groups.py

Consolidate people groups using alias system.

**Usage:**
```bash
python3 scripts/consolidate_people_groups.py
```

**Features:**
- Uses group aliases from config
- Merges groups with same canonical name
- Preserves all mentions
- Updates references

---

### suggest_group_aliases.py

Suggest aliases for people groups.

**Usage:**
```bash
python3 scripts/suggest_group_aliases.py
```

**Output:** Suggested aliases for manual review

**Use Case:** Identify groups that should be consolidated

---

## Metadata Management

### complete_metadata_with_grok.py

Complete missing metadata fields using Grok AI.

**Usage:**
```bash
python3 scripts/complete_metadata_with_grok.py
```

**Completes:**
- Chapter titles
- Chapter numbers
- Author names
- Book titles

**Use Case:** After running generate_missing_metadata.py

---

### generate_missing_metadata.py

Generate metadata YAML templates for chapters.

**Usage:**
```bash
python3 scripts/generate_missing_metadata.py
```

**Creates:** `chapter*-meta.yaml` files with templates

**Use Case:** Adding new books to contentrepository

---

### standardize_metadata.py

Standardize metadata format across all chapters.

**Usage:**
```bash
python3 scripts/standardize_metadata.py
```

**Standardizes:**
- Field names
- Date formats
- License strings
- Author names

---

## Content Processing

### pdf_to_markdown.py

Convert PDF files to markdown format.

**Usage:**
```bash
python3 scripts/pdf_to_markdown.py document.pdf "BookName"
```

**Features:**
- Extracts text from PDF
- Converts to markdown
- Preserves structure
- Creates chapter directories

**Requires:** `pymupdf` (PyMuPDF)

**See:** [PDF_CONVERSION.md](../docs/current/pipeline/PDF_CONVERSION.md)

---

### split_chapters.py

Split large chapters into smaller sections.

**Usage:**
```bash
python3 scripts/split_chapters.py chapter.md --paragraphs 50
```

**Options:**
- `--paragraphs N` - Split every N paragraphs
- `--output-dir DIR` - Output directory

**Use Case:** Chapters too large for API processing

---

### process_supplemental_info.py

Process supplemental material extraction.

**Usage:**
```bash
python3 scripts/process_supplemental_info.py
```

**Features:**
- Extracts citations
- Validates URLs
- Searches for online versions
- Determines copyright status

---

## Validation

### validate_data.py

Validate all extracted data against schemas.

**Usage:**
```bash
python3 scripts/validate_data.py
```

**Validates:**
- Events
- Dates
- Places
- People
- People Groups
- Equipment
- All JSON schemas

**Output:** Validation report with errors

---

### validate_places.py

Validate place data specifically.

**Usage:**
```bash
python3 scripts/validate_places.py
```

**Checks:**
- Coordinate validity
- Required fields
- Country codes (ISO 3166-1 alpha-3)
- Bounding boxes

---

### validate_supplemental_urls.py

Validate URLs in supplemental materials.

**Usage:**
```bash
python3 scripts/validate_supplemental_urls.py
```

**Checks:**
- URL accessibility (HEAD request)
- Response codes
- Redirects
- Timeouts

**Output:** Report of broken/invalid URLs

---

### validation_report.py

Generate comprehensive validation report.

**Usage:**
```bash
python3 scripts/validation_report.py
```

**Output:** `validation_report.html`

**Includes:**
- Schema validation results
- Data quality metrics
- Coverage statistics
- Error summaries

---

## Utilities

### review_cache.py

Review and analyze API cache contents.

**Usage:**
```bash
python3 scripts/review_cache.py
```

**Shows:**
- Cache size by type
- Hit/miss rates
- Oldest/newest entries
- Cache efficiency

**Options:**
- `--clear TYPE` - Clear specific cache type
- `--stats` - Show statistics only

---

### extract_url.py

Extract URLs from data files.

**Usage:**
```bash
python3 scripts/extract_url.py
```

**Extracts URLs from:**
- Supplemental materials
- Map references
- External sources

**Output:** List of all URLs

---

### generate_dashboard.py

Generate validation dashboard HTML.

**Usage:**
```bash
python3 scripts/generate_dashboard.py
```

**Output:** `validation_dashboard.html`

**Features:**
- Interactive charts
- Data quality metrics
- Coverage visualization
- Error tracking

---

### generate_schema_docs.py

Generate documentation from JSON schemas.

**Usage:**
```bash
python3 scripts/generate_schema_docs.py
```

**Output:** Schema documentation in markdown

**Use Case:** Keep schema docs in sync with code

---

### generate_type_stubs.py

Generate Python type stubs from schemas.

**Usage:**
```bash
python3 scripts/generate_type_stubs.py
```

**Output:** `.pyi` type stub files

**Use Case:** Type checking with mypy

---

## Archived Scripts

Scripts moved to `scripts/archive/` (no longer needed):

### QA Scripts (archive/qa/)
- `qa_concurrent.py` - QA for concurrent changes
- `qa_logistics.py` - QA for logistics
- `check_black.py` - Black formatter check
- `format_files.py` - Format specific files

### Migration Scripts (archive/migration/)
- `migrate_people_schema.py` - Migrate people schema (completed)
- `migrate_place_schema.py` - Migrate place schema (completed)
- `migrate_schema.py` - General schema migration (completed)
- `verify_requests_migration.py` - Verify httpx→requests (completed)
- `deduplicate_ranks.py` - Deduplicate ranks (completed)
- `fix_place_map_urls.py` - Fix place map URLs (completed)

### Testing Scripts (archive/testing/)
- `test_grok_api.py` - Test Grok API connection
- `test_place_extraction.py` - Test place extraction
- `verify_phase2_setup.py` - Setup verification

### Obsolete Scripts (archive/)
- `verify_and_import.py` - Import verification (obsolete)
- `enrich_equipment.py` - Equipment enrichment (now in extraction)

---

## Common Workflows

### After Phase 2 Extraction

```bash
# 1. Find duplicates
python3 scripts/find_duplicate_people.py
python3 scripts/find_duplicate_places.py
python3 scripts/find_related_groups.py

# 2. Review and merge
python3 scripts/merge_duplicate_people.py
python3 scripts/merge_duplicate_places.py
python3 scripts/merge_related_groups.py

# 3. Validate data
python3 scripts/validate_data.py

# 4. Generate report
python3 scripts/validation_report.py
```

### Adding New Content

```bash
# 1. Convert PDF (if needed)
python3 scripts/pdf_to_markdown.py book.pdf "BookName"

# 2. Generate metadata templates
python3 scripts/generate_missing_metadata.py

# 3. Complete metadata with AI
python3 scripts/complete_metadata_with_grok.py

# 4. Standardize format
python3 scripts/standardize_metadata.py

# 5. Run pipeline
python3 phase1_parse.py
python3 phase2_retry.py
```

### Data Quality Check

```bash
# 1. Validate all data
python3 scripts/validate_data.py

# 2. Check places specifically
python3 scripts/validate_places.py

# 3. Validate supplemental URLs
python3 scripts/validate_supplemental_urls.py

# 4. Generate dashboard
python3 scripts/generate_dashboard.py

# 5. Open dashboard
open validation_dashboard.html
```

---

## Related Documentation

- [Pipeline Overview](../docs/current/core/PIPELINE.md)
- [Configuration](../docs/current/core/CONFIGURATION.md)
- [Error Handling](../docs/current/core/error_handling.md)
- [People Deduplication](../docs/current/features/people/deduplication.md)
- [Group Deduplication](../docs/current/features/people/GROUP_DEDUPLICATION_SYSTEM.md)
