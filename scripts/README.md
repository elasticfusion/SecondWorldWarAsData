# Scripts Reference

**Last Updated:** 2026-04-19

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
- [find_duplicate_equipment.py](#find_duplicate_equipmentpy)
- [find_related_groups.py](#find_related_groupspy)
- [merge_related_groups.py](#merge_related_groupspy)
- [merge_equipment.py](#merge_equipmentpy)
- [consolidate_people_groups.py](#consolidate_people_groupspy)
- [consolidate_places.py](#consolidate_placespy)
- [suggest_group_aliases.py](#suggest_group_aliasespy)

### Metadata Management
- [complete_metadata_with_grok.py](#complete_metadata_with_grokpy)
- [generate_missing_metadata.py](#generate_missing_metadatapy)
- [standardize_metadata.py](#standardize_metadatapy)

### Content Processing
- [import_hyperwar_html.py](#import_hyperwar_htmlpy)
- [pdf_to_markdown.py](#pdf_to_markdownpy)
- [split_chapters.py](#split_chapterspy)
- [process_supplemental_info.py](#process_supplemental_infopy)

### Validation
- [validate_data.py](#validate_datapy)
- [validate_output.py](#validate_outputpy)
- [validate_places.py](#validate_placespy)
- [validate_supplemental_urls.py](#validate_supplemental_urlspy)
- [validation_report.py](#validation_reportpy)

### Data Backfill
- [backfill_date_fields.py](#backfill_date_fieldspy)
- [backfill_equipment_media.py](#backfill_equipment_mediapy)
- [backfill_group_fields.py](#backfill_group_fieldspy)
- [fix_place_map_urls.py](#fix_place_map_urlspy)

### Utilities
- [benchmark_performance.py](#benchmark_performancepy)
- [render_mermaid_diagrams.py](#render_mermaid_diagramspy)
- [review_cache.py](#review_cachepy)
- [extract_url.py](#extract_urlpy)
- [generate_dashboard.py](#generate_dashboardpy)
- [generate_schema_docs.py](#generate_schema_docspy)
- [generate_type_stubs.py](#generate_type_stubspy)

### Shell Scripts
- [run_tests.sh](#run_testssh)
- [qa_check_tests.sh](#qa_check_testssh)
- [cleanup_people.sh](#cleanup_peoplesh)
- [test_grok_search.sh](#test_grok_searchsh)
- [test_blacklist_comments.sh](#test_blacklist_commentssh)
- [archive_scripts.sh](#archive_scriptssh)
- [archive_merge_duplicate_groups.sh](#archive_merge_duplicate_groupssh)

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

### find_duplicate_equipment.py

Find potential duplicate equipment records based on name similarity and shared attributes.

**Usage:**
```bash
python3 scripts/find_duplicate_equipment.py
```

**Output:** `output/equipment/duplicate_report.json`

**Detection Methods:**
- Name similarity (70%+ threshold) across common_name, technical_identifier, and alternate_names
- Caliber/designation normalization (`.50-caliber` ↔ `50 caliber`, `155-mm` ↔ `155mm`)
- Name containment checks (shorter name >60% of longer)
- Same-category matching

**Respects:** `output/equipment/not_duplicates.json` exclusion list

**Next Step:** Review report and run `merge_equipment.py`

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

### consolidate_places.py

Apply `config/place_aliases.yaml` hierarchy and aliases to existing place files.

**Usage:**
```bash
python3 scripts/consolidate_places.py [--dry-run]
```

**Options:**
- `--dry-run` - Show which files would be updated without writing

**Applies:**
- Geographic hierarchy (continent > country > region) from `hierarchies` config
- Canonical aliases from `aliases` and `geographic_features` config
- Historical names from `name_changes` entries

**Requires:** `config/place_aliases.yaml`

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

### import_hyperwar_html.py

Download HyperWar HTML chapters from ibiblio.org, convert to markdown, and generate metadata.

**Usage:**
```bash
python3 scripts/import_hyperwar_html.py <index_url>
```

**Example:**
```bash
python3 scripts/import_hyperwar_html.py https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html
```

**Process:**
1. Parses index page to find chapter links
2. Downloads each chapter HTML page
3. Converts HTML to markdown (preserves footnotes, images, page markers)
4. Splits chapters into sub-chapters (a, b, c...) at section headings
5. Prompts for metadata (series, book, author, license)
6. Creates directory structure under `contentrepository/`

**Interactive:** Prompts for book metadata during import

**Requires:** `html2text`, `beautifulsoup4`, `requests`, `pyyaml`

**See:** [HYPERWAR_HTML_IMPORT.md](../docs/current/pipeline/HYPERWAR_HTML_IMPORT.md)

---

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

### validate_output.py

Validate all `output/` JSON files against built-in schemas for structural correctness.

**Usage:**
```bash
python3 scripts/validate_output.py
```

**Checks:**
- Required fields per entity type (events, dates, places, people, groups, equipment, casualties, weather, logistics, maps, bibliography, supplemental)
- Valid ULID format on all ID fields and MentionIDs
- Type consistency (strings, lists, dicts)
- Nested structure (Event wrapper, array-of-objects for supplemental)

**Exit code:** 0 if all valid, 1 if any errors found

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

## Data Backfill

One-shot scripts for backfilling spec-level fields on existing data files after schema changes.

### backfill_date_fields.py

Backfill spec-level fields on existing date files.

**Usage:**
```bash
python3 scripts/backfill_date_fields.py [--dry-run]
```

**Options:**
- `--dry-run` - Show which files would be updated without writing

**Changes:**
- Renames `date` → `date_start`
- Adds missing fields with defaults: `date_end`, `time_start`, `time_end`, `time_precision`, `date_precision`, `time_source`, `original_text`, `normalized_datetime`
- Infers `date_precision` from `date_start` format (early/mid/late/seasonal → prefix, otherwise → exact)

---

### backfill_equipment_media.py

Backfill equipment files with images from OpenSERP and Wikipedia.

**Usage:**
```bash
python3 scripts/backfill_equipment_media.py [--dry-run]
```

**Options:**
- `--dry-run` - Show what would be downloaded without writing files

**Strategy:**
1. OpenSERP (if running) — searches Google/Bing/DuckDuckGo, filters to Wikipedia/Commons/Archive sources, scrapes actual image URLs
2. Wikipedia API fallback — direct MediaWiki API for article images

**Downloads to:** `/filestore/equipment/<EquipmentID>/`

**Skips:** Equipment files that already have a `media` field

**Requires:** `requests`, `beautifulsoup4`; optionally OpenSERP running on port 7001

---

### backfill_group_fields.py

Backfill spec-level fields on existing people_group files from enrichment data.

**Usage:**
```bash
python3 scripts/backfill_group_fields.py [--dry-run]
```

**Options:**
- `--dry-run` - Show which files would be updated without writing

**Changes:**
- Adds `group_name` from `name` if missing
- Promotes `enrichment_data` fields to top-level using `_promote_enrichment()`

---

### fix_place_map_urls.py

Backfill missing map URLs and bounding boxes on place files that have coordinates.

**Usage:**
```bash
python3 scripts/fix_place_map_urls.py [--dry-run]
```

**Options:**
- `--dry-run` - Show which files would be updated without writing

**Adds:**
- `map_urls` with Google Maps and OpenStreetMap links
- `bounding_box_100km` (~0.9° offset from coordinates)

**Skips:** Files without valid latitude/longitude

---

## Utilities

### benchmark_performance.py

Benchmark pipeline performance and generate a markdown report.

**Usage:**
```bash
python3 scripts/benchmark_performance.py
```

**Measures:**
- Phase 1: chapter count, file sizes, paragraph counts
- Phase 2: entity counts by type (events, dates, places, people, groups)
- Cache: size by type, total files, storage used
- Memory: RSS and VMS usage

**Output:** `docs/current/qa-reports/PERFORMANCE_BENCHMARK_<date>.md`

**Requires:** `psutil`

---

### render_mermaid_diagrams.py

Render Mermaid code blocks from markdown files to PNG images.

**Usage:**
```bash
python3 scripts/render_mermaid_diagrams.py [markdown_file ...]
```

**Default:** Processes `docs/current/core/WORKFLOW_DIAGRAMS.md`

**Features:**
- Extracts ` ```mermaid ` blocks and associates them with nearest `##` heading
- Content-hash based change detection — only re-renders when diagram source changes
- Outputs PNGs to sibling `images/` directory
- Maintains `mermaid_manifest.json` mapping diagram names to content hashes

**Requires:** `npx @mermaid-js/mermaid-cli` (auto-installed via npx)

---

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

## Shell Scripts

### run_tests.sh

Test runner with multiple modes.

**Usage:**
```bash
./scripts/run_tests.sh [mode]
```

**Modes:**
- `unit` - Run unit tests only
- `integration` - Run integration tests only
- `fast` - Unit tests with minimal output
- `coverage` - Tests with HTML coverage report (`htmlcov/index.html`)
- `watch` - Watch mode (requires `pytest-watch`)
- `all` - Run all tests (default)

---

### qa_check_tests.sh

Run QA checks on test files.

**Usage:**
```bash
./scripts/qa_check_tests.sh
```

**Runs:**
1. Black formatting check
2. Mypy type checking
3. Pylint code quality scoring
4. Bandit security scan
5. Radon complexity analysis
6. Radon maintainability index

---

### cleanup_people.sh

Delete old people files and cache before re-running extraction.

**Usage:**
```bash
./scripts/cleanup_people.sh
```

**Deletes:**
- `output/*-people.json` (old per-chapter format)
- `output/*/people-central.json`, `output/*/people-consolidated.json`
- `output/people/*.json`
- `cache/api/people/*`

**Use Case:** Reset people data before a clean re-extraction

---

### test_grok_search.sh

Quick test of combined map search (Grok whitelist + OpenSERP).

**Usage:**
```bash
./scripts/test_grok_search.sh
```

**Runs:** `python3 -m src.extraction.combined_map_search --max-places 5`

**Interactive:** Prompts before starting

---

### test_blacklist_comments.sh

Demo script showing blacklist comment functionality with mock data.

**Usage:**
```bash
./scripts/test_blacklist_comments.sh
```

**Demonstrates:** How `search_maps` appends filter comments to `domain_blacklist.yaml` when URLs are blocked. Creates and cleans up temporary test files.

---

### archive_scripts.sh

One-shot script to archive temporary and obsolete scripts into `archive/` subdirectories.

**Usage:**
```bash
./scripts/archive_scripts.sh
```

**Moves scripts to:** `archive/qa/`, `archive/testing/`, `archive/migration/`, `archive/obsolete/`

---

### archive_merge_duplicate_groups.sh

One-shot script to archive `merge_duplicate_groups.py` (superseded by `merge_related_groups.py`).

**Usage:**
```bash
./scripts/archive_merge_duplicate_groups.sh
```

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
python3 scripts/find_duplicate_equipment.py
python3 scripts/find_related_groups.py

# 2. Review and merge
python3 scripts/merge_duplicate_people.py
python3 scripts/merge_duplicate_places.py
python3 scripts/merge_related_groups.py

# 3. Validate data
python3 scripts/validate_data.py
python3 scripts/validate_output.py

# 4. Generate report
python3 scripts/validation_report.py
```

### Adding New Content

```bash
# 1a. From HyperWar HTML
python3 scripts/import_hyperwar_html.py <index_url>

# 1b. Or from PDF
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

# 2. Structural validation
python3 scripts/validate_output.py

# 3. Check places specifically
python3 scripts/validate_places.py

# 4. Validate supplemental URLs
python3 scripts/validate_supplemental_urls.py

# 5. Generate dashboard
python3 scripts/generate_dashboard.py

# 6. Open dashboard
open validation_dashboard.html
```

---

## Related Documentation

- [Pipeline Overview](../docs/current/core/PIPELINE.md)
- [Configuration](../docs/current/core/CONFIGURATION.md)
- [Error Handling](../docs/current/core/error_handling.md)
- [People Deduplication](../docs/current/features/people/deduplication.md)
- [Group Deduplication](../docs/current/features/people/GROUP_DEDUPLICATION_SYSTEM.md)
