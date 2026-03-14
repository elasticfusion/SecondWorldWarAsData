# Documentation Index

**Last Updated:** 2026-03-13

---

## Quick Start

- **[README.md](../../README.md)** - Project overview and quick start
- **[core/DEVELOPMENT.md](core/DEVELOPMENT.md)** - Setup and development workflow
- **[core/PIPELINE.md](core/PIPELINE.md)** - Complete pipeline documentation
- **[pipeline/RETRY_WRAPPERS.md](pipeline/RETRY_WRAPPERS.md)** - Automatic retry for Phase 2 & 3

---

## Quality Assurance (NEW - 2026-03-13)

Recent code quality improvements and reports.

- **[qa-reports/QA_REPORT_2026-03-13.md](qa-reports/QA_REPORT_2026-03-13.md)** - ✅ Complete QA report (all targets met)
- **[qa-reports/CODE_QUALITY_IMPROVEMENTS_2026-03-13.md](qa-reports/CODE_QUALITY_IMPROVEMENTS_2026-03-13.md)** - 40 functions refactored (66% complexity reduction)
- **[qa-reports/REFACTORING_SUMMARY_2026-03-13.md](qa-reports/REFACTORING_SUMMARY_2026-03-13.md)** - Detailed refactoring metrics
- **[qa-reports/CENTRALIZATION_RECOMMENDATIONS.md](qa-reports/CENTRALIZATION_RECOMMENDATIONS.md)** - Code consolidation (125+ lines removed)
- **[qa-reports/RADON_COMPLEXITY_2026-03-13.md](qa-reports/RADON_COMPLEXITY_2026-03-13.md)** - Complexity analysis

**Key Achievements:**
- ✅ All C and D grade functions eliminated (100%)
- ✅ Average complexity reduced by 66%
- ✅ ULID validation centralized (5 files)
- ✅ 125+ lines of duplicate code removed
- ✅ Pylint: 9.35/10, Mypy: 0 errors, Bandit: 0 issues

---

## Core Documentation

Essential architecture, configuration, and API documentation.

### Architecture & Configuration

- **[CODE_ARCHITECTURE.md](core/CODE_ARCHITECTURE.md)** - Code structure and design patterns
- **[API_REFERENCE.md](core/API_REFERENCE.md)** - Complete API documentation
- **[CONFIGURATION.md](core/CONFIGURATION.md)** - config.yaml reference guide
- **[DEVELOPMENT.md](core/DEVELOPMENT.md)** - Setup and development workflow
- **[PIPELINE.md](core/PIPELINE.md)** - Phase 1 & 2 pipeline overview
- **[WORKFLOW_DIAGRAMS.md](core/WORKFLOW_DIAGRAMS.md)** - Visual workflow diagrams
- **[error_handling.md](core/error_handling.md)** - Error handling patterns (25 patterns)
- **[ULID_IMPLEMENTATION.md](core/ULID_IMPLEMENTATION.md)** - ULID identifier system

### Standards

- **[ISO_COUNTRY_CODES.md](core/ISO_COUNTRY_CODES.md)** - ISO 3166-1 alpha-3 country codes
- **[JSON_REPAIR.md](core/JSON_REPAIR.md)** - Automatic JSON repair patterns
- **[CACHE_AUTO_RECOVERY.md](core/CACHE_AUTO_RECOVERY.md)** - Cache corruption auto-recovery

### Testing

- **[TESTING.md](core/TESTING.md)** - Testing framework and best practices
- **[TEST_STATUS.md](core/testing/TEST_STATUS.md)** - Current test coverage status
- **[TEST_DIRECTORY_CLEANUP.md](core/testing/TEST_DIRECTORY_CLEANUP.md)** - Test directory organization
- **[TESTING_IMPROVEMENTS.md](core/testing/TESTING_IMPROVEMENTS.md)** - Testing improvements log

### Scripts

- **[SCRIPT_ERROR_HANDLING.md](core/scripts/SCRIPT_ERROR_HANDLING.md)** - Script error handling patterns
- **[SCRIPTS_REVIEW.md](core/scripts/SCRIPTS_REVIEW.md)** - Scripts review and analysis
- **[MERGE_SCRIPTS_COMPARISON.md](core/scripts/MERGE_SCRIPTS_COMPARISON.md)** - Merge script comparison

---

## Pipeline

Data ingestion and processing workflows.

- **[RETRY_WRAPPERS.md](pipeline/RETRY_WRAPPERS.md)** - Automatic retry for Phase 2 & 3 ⭐
- **[PHASE1_IMPLEMENTATION_SUMMARY.md](pipeline/PHASE1_IMPLEMENTATION_SUMMARY.md)** - Phase 1 implementation
- **[PHASE2_COMPLETE.md](pipeline/PHASE2_COMPLETE.md)** - Phase 2 extraction guide
- **[PHASE3_COMPLETE.md](pipeline/PHASE3_COMPLETE.md)** - Phase 3 enrichment guide
- **[PHASE2_REPROCESSING_ISSUES.md](pipeline/PHASE2_REPROCESSING_ISSUES.md)** - Reprocessing troubleshooting
- **[ADDING_DATA_SOURCES.md](pipeline/ADDING_DATA_SOURCES.md)** - How to add new books/content
- **[PAPERS_AND_ARTICLES.md](pipeline/PAPERS_AND_ARTICLES.md)** - Handling papers, articles, and non-chapter documents
- **[PDF_CONVERSION.md](pipeline/PDF_CONVERSION.md)** - Converting PDFs to markdown

---

## Features

### External Maps

External map discovery from archives, museums, and historical sites.

- **[README.md](features/external-maps/README.md)** - Overview and main documentation
- **[openserp-integration.md](features/external-maps/openserp-integration.md)** - OpenSERP search integration
- **[grok-search.md](features/external-maps/grok-search.md)** - Grok search-based discovery
- **[grok-implementation.md](features/external-maps/grok-implementation.md)** - Grok search technical details
- **[combined-search.md](features/external-maps/combined-search.md)** - Two-phase search strategy
- **[vision-verification.md](features/external-maps/vision-verification.md)** - Grok vision API verification
- **[image-processing.md](features/external-maps/image-processing.md)** - Image validation and processing
- **[domain-blacklist.md](features/external-maps/domain-blacklist.md)** - URL filtering configuration
- **[search-history.md](features/external-maps/search-history.md)** - Search history tracking
- **[whitelist-config.md](features/external-maps/whitelist-config.md)** - YAML whitelist configuration

### People Management

Person extraction, deduplication, and group management.

- **[README.md](features/people/README.md)** - Overview and file-per-person architecture
- **[implementation.md](features/people/implementation.md)** - Implementation details
- **[deduplication.md](features/people/deduplication.md)** - Duplicate detection strategy
- **[duplicate-exclusions.md](features/people/duplicate-exclusions.md)** - False positive prevention
- **[groups.md](features/people/groups.md)** - Organizations and military units
- **[GROUP_DEDUPLICATION_SYSTEM.md](features/people/GROUP_DEDUPLICATION_SYSTEM.md)** - Group deduplication v2.0 ⭐

### Maps

Map extraction from source material.

- **[README.md](features/maps/README.md)** - Maps extraction from source material
- **[S3_STORAGE.md](features/maps/S3_STORAGE.md)** - S3 storage backend configuration

### Military Equipment

Military equipment extraction and tracking (experimental).

- **[MILITARY_EQUIPMENT.md](features/equipment/MILITARY_EQUIPMENT.md)** - Complete proposal and examples
- **[MILITARY_EQUIPMENT_SUMMARY.md](features/MILITARY_EQUIPMENT_SUMMARY.md)** - Quick summary
- **[EQUIPMENT_FINAL_STRUCTURE.md](features/equipment/EQUIPMENT_FINAL_STRUCTURE.md)** - Final schema structure
- **[EQUIPMENT_PEOPLE_PATTERN.md](features/equipment/EQUIPMENT_PEOPLE_PATTERN.md)** - Pattern comparison
- **[EQUIPMENT_DESCRIPTION_FIELDS.md](features/equipment/EQUIPMENT_DESCRIPTION_FIELDS.md)** - Description fields

### Supplemental Material

Citations, footnotes, endnotes, and bibliographic references.

- **[SUPPLEMENTAL_COMPLETE.md](features/supplemental/SUPPLEMENTAL_COMPLETE.md)** - Complete implementation guide ⭐
- **[SUPPLEMENTAL_PHASE1.md](features/supplemental/SUPPLEMENTAL_PHASE1.md)** - Phase 1: Basic extraction
- **[SUPPLEMENTAL_PHASE2.md](features/supplemental/SUPPLEMENTAL_PHASE2.md)** - Phase 2: URL search and enrichment
- **[SUPPLEMENTAL_VALIDATION.md](features/supplemental/SUPPLEMENTAL_VALIDATION.md)** - URL validation
- **[SUPPLEMENTAL_ERROR_HANDLING.md](features/supplemental/SUPPLEMENTAL_ERROR_HANDLING.md)** - Error handling
- **[SUPPLEMENTAL_TESTING.md](features/supplemental/SUPPLEMENTAL_TESTING.md)** - Testing guide
- **[SUPPLEMENTAL_QA_RESULTS.md](features/supplemental/SUPPLEMENTAL_QA_RESULTS.md)** - QA results

### Concurrency

Concurrent processing for improved performance.

- **[CONCURRENCY_ANALYSIS.md](features/concurrency/CONCURRENCY_ANALYSIS.md)** - Concurrency analysis and design
- **[HYBRID_CONCURRENT_IMPLEMENTATION.md](features/concurrency/HYBRID_CONCURRENT_IMPLEMENTATION.md)** - Implementation guide
- **[QA_CONCURRENT.md](features/concurrency/QA_CONCURRENT.md)** - Concurrency QA report

### Casualties

Casualty tracking (experimental).

- **[README.md](features/casualties/README.md)** - Casualties feature documentation

---

## Quality Assurance

QA reports and improvement documentation.

- **[2026-03-02-grok-search.md](qa-reports/2026-03-02-grok-search.md)** - Grok search QA report
- **[grok-search-improvements.md](qa-reports/grok-search-improvements.md)** - Quality improvements
- **[EQUIPMENT_QA_REPORT.md](qa-reports/EQUIPMENT_QA_REPORT.md)** - Equipment feature QA
- **[2026-03-03-testing-code.md](qa-reports/2026-03-03-testing-code.md)** - Testing code QA
- **[QA_BLACK.md](qa-reports/QA_BLACK.md)** - Black formatter QA
- **[QA_LOGISTICS.md](qa-reports/QA_LOGISTICS.md)** - Logistics feature QA
- **[QA_VULTURE.md](qa-reports/QA_VULTURE.md)** - Dead code detection QA

---

## Changelog

- **[CHANGELOG_2026_03_08.md](CHANGELOG_2026_03_08.md)** - Latest changes (retry wrappers, group dedup v2.0)

---

## Archived Documentation

Historical documentation organized by date and subject matter.

Located in: `../archive/`

### Recent Archives

- **2026-03-08/** - Today's implementation reports (supplemental, QA, group dedup)
- **changelogs/** - Historical changelogs (2026-03-02, 2026-03-04, reorganization)
- **bug-fixes/** - Bug fix documentation (2026-03-05)
- **migrations/** - Migration guides (httpx→requests)
- **2026-03-02/** - Previous reorganization and consolidation

### Subject Archives

- **core/** - Core architecture archives
- **external-maps/** - External maps development history
- **people/** - People management archives
- **pipeline/** - Pipeline development history
- **qa-reports/** - Historical QA reports
- **misc/** - Miscellaneous documentation

---

**Navigation:** Use this index to find documentation. All paths are relative to `docs/current/`.

**Contributing:** Follow the established structure when adding new documentation:
- Core docs → `core/`
- Pipeline docs → `pipeline/`
- Feature docs → `features/{feature-name}/`
- QA reports → `qa-reports/`
- Dated docs → Archive after consolidation

**Last Updated:** 2026-03-08
