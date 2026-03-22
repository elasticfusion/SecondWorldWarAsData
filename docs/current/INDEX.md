# Documentation Index

**Last Updated:** 2026-03-22

---

## Quick Start

- **[README.md](../../README.md)** - Project overview and quick start
- **[core/DEVELOPMENT.md](core/DEVELOPMENT.md)** - Setup and development workflow
- **[core/PIPELINE.md](core/PIPELINE.md)** - Complete pipeline documentation
- **[pipeline/RETRY_WRAPPERS.md](pipeline/RETRY_WRAPPERS.md)** - Automatic retry for Phase 2 & 3

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
- **[PROMPT_MANAGEMENT.md](core/PROMPT_MANAGEMENT.md)** - Grok prompt inventory, patterns, and modification guide
- **[ULID_IMPLEMENTATION.md](core/ULID_IMPLEMENTATION.md)** - ULID identifier system

### Standards

- **[ISO_COUNTRY_CODES.md](core/ISO_COUNTRY_CODES.md)** - ISO 3166-1 alpha-3 country codes
- **[JSON_REPAIR.md](core/JSON_REPAIR.md)** - Automatic JSON repair patterns
- **[CACHE_AUTO_RECOVERY.md](core/CACHE_AUTO_RECOVERY.md)** - Cache corruption auto-recovery

### Testing

- **[TESTING.md](core/TESTING.md)** - Testing framework and best practices

### Scripts

See [scripts/README.md](../../scripts/README.md) for the complete scripts reference.

---

## Pipeline

Data ingestion and processing workflows.

- **[RETRY_WRAPPERS.md](pipeline/RETRY_WRAPPERS.md)** - Automatic retry for Phase 2 & 3 ⭐
- **[ADDING_DATA_SOURCES.md](pipeline/ADDING_DATA_SOURCES.md)** - How to add new books/content
- **[PAPERS_AND_ARTICLES.md](pipeline/PAPERS_AND_ARTICLES.md)** - Handling papers, articles, and non-chapter documents
- **[PDF_CONVERSION.md](pipeline/PDF_CONVERSION.md)** - Converting PDFs to markdown
- **[HYPERWAR_HTML_IMPORT.md](pipeline/HYPERWAR_HTML_IMPORT.md)** - HyperWar HTML import

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
- **[biographical-enrichment.md](features/people/biographical-enrichment.md)** - Phase 3 biographical enrichment ⭐
- **[deduplication.md](features/people/deduplication.md)** - Duplicate detection strategy
- **[duplicate-exclusions.md](features/people/duplicate-exclusions.md)** - False positive prevention
- **[groups.md](features/people/groups.md)** - Organizations and military units
- **[GROUP_DEDUPLICATION_SYSTEM.md](features/people/GROUP_DEDUPLICATION_SYSTEM.md)** - Group deduplication v2.0

### Maps

Map extraction from source material.

- **[README.md](features/maps/README.md)** - Maps extraction from source material
- **[S3_STORAGE.md](features/maps/S3_STORAGE.md)** - S3 storage backend configuration

### Military Equipment

Military equipment extraction and tracking (experimental).

- **[MILITARY_EQUIPMENT.md](features/equipment/MILITARY_EQUIPMENT.md)** - Complete proposal and examples
- **[MILITARY_EQUIPMENT_SUMMARY.md](features/MILITARY_EQUIPMENT_SUMMARY.md)** - Quick summary
- **[EQUIPMENT_FINAL_STRUCTURE.md](features/equipment/EQUIPMENT_FINAL_STRUCTURE.md)** - Final schema structure
- **[EQUIPMENT_DEDUPLICATION.md](features/equipment/EQUIPMENT_DEDUPLICATION.md)** - Equipment deduplication
- **[EQUIPMENT_ENTITY_LINKING.md](features/equipment/EQUIPMENT_ENTITY_LINKING.md)** - Entity linking
- **[EQUIPMENT_MEDIA_INTEGRATION.md](features/equipment/EQUIPMENT_MEDIA_INTEGRATION.md)** - Media integration
- **[EQUIPMENT_ERROR_HANDLING.md](features/equipment/EQUIPMENT_ERROR_HANDLING.md)** - Error handling

### Supplemental Material

Citations, footnotes, endnotes, and bibliographic references.

- **[SUPPLEMENTAL_COMPLETE.md](features/supplemental/SUPPLEMENTAL_COMPLETE.md)** - Complete implementation guide (split architecture) ⭐
- **[SUPPLEMENTAL_VALIDATION.md](features/supplemental/SUPPLEMENTAL_VALIDATION.md)** - Validation and ULID fixing
- **[SUPPLEMENTAL_ERROR_HANDLING.md](features/supplemental/SUPPLEMENTAL_ERROR_HANDLING.md)** - Error handling
- **[SUPPLEMENTAL_TESTING.md](features/supplemental/SUPPLEMENTAL_TESTING.md)** - Testing guide

### Batch Processing

Parallel chapter processing and batched API calls for both core and optional extractors.

- **[batch_processing/README.md](features/batch_processing/README.md)** - Batch and parallel processing architecture ⭐

---

## Reference

- **[SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md)** - JSON schema reference for all 11 entity types, cross-reference conventions
- **[VALIDATION_REPORTS.md](VALIDATION_REPORTS.md)** - Validation report generation
- **[MONGODB_IMPORT_PLAN.md](MONGODB_IMPORT_PLAN.md)** - MongoDB import plan
- **[FUTURE_ENHANCEMENTS.md](FUTURE_ENHANCEMENTS.md)** - Distributed processing options
- **[TODO.md](TODO.md)** - Outstanding tasks
- **[TEXT_UTILS.md](core/TEXT_UTILS.md)** - Text utility functions

---

## Archive

Historical docs moved to `docs/archive/` — includes phase implementation logs, point-in-time reviews, and superseded schema docs.

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

**Last Updated:** 2026-03-19
