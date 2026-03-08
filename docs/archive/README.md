# Archived Documentation

**Last Updated:** 2026-03-02  
**Status:** Consolidated from `docs/archive` and `docs/current/archived`

This directory contains historical documentation organized by subject matter.

---

## Directory Structure

```
archive/
├── core/              # 5 files - Core architecture & API
├── external-maps/     # 17 files - External maps feature history
├── people/            # 3 files - People management history
├── pipeline/          # 8 files - Pipeline development history
├── qa-reports/        # 4 files - Historical QA reports
└── misc/              # 24 files - Misc planning, reviews, schemas
```

---

## Contents by Category

### Core (5 files)
Historical core architecture and API documentation:
- `CACHE_REVIEW.md` - Cache structure review
- `CACHE_STRUCTURE.md` - Cache organization
- `GROK_API_FLOW.md` - Grok API flow documentation
- `GROK_PLACE_EXTRACTION_ISSUE.md` - Place extraction issues
- `METADATA.md` - Old metadata system (deprecated)

**Superseded by:** `../current/core/`

---

### External Maps (17 files)
Complete history of external maps feature development:
- `EXTERNAL_MAPS_*.md` - Implementation, features, guides, fixes
- `ANTI_HALLUCINATION_STRATEGY.md` - Anti-hallucination approach
- `BLACKLIST_AUDIT_TRAIL.md` - Blacklist audit history
- `CHANGELOG_2026_02_26.md` - February 26 changelog
- `GROK_VERIFICATION.md` - Old verification docs
- `HALLUCINATION_FIX.md` - Hallucination prevention fixes
- `LOC_URL_FIX.md` - Library of Congress URL fixes
- `WHITELIST_FEATURE.md` - Old whitelist feature docs

**Superseded by:** `../current/features/external-maps/`

---

### People (3 files)
People management feature development history:
- `PEOPLE_CENTRAL_MANAGEMENT.md` - Central management approach
- `PEOPLE_INTEGRATION_COMPLETE.md` - Integration completion
- `PEOPLE_SINGLE_FILE.md` - Single file per person approach

**Superseded by:** `../current/features/people/`

---

### Pipeline (8 files)
Pipeline development and fixes:
- `PHASE1_*.md` - Phase 1 development and completion
- `PHASE2_*.md` - Phase 2 status and readme
- `DUPLICATE_PLACES_FIX.md` - Duplicate place fixes
- `MAP_URLS_FIX.md` - Map URL fixes
- `DATES_STRUCTURED_OUTPUTS_COMPLETE.md` - Date extraction completion

**Superseded by:** `../current/core/PIPELINE.md` and `../current/pipeline/`

---

### QA Reports (4 files)
Historical quality assurance reports:
- `CODE_QUALITY_FINAL.md` - Final code quality report
- `CODE_QUALITY_REPORT.md` - Code quality report
- `EXTERNAL_MAPS_QA_REPORT_FINAL.md` - External maps final QA
- `EXTERNAL_MAPS_QA_REPORT.md` - External maps QA

**Superseded by:** `../current/qa-reports/`

---

### Misc (24 files)
Planning documents, reviews, schema migrations, and miscellaneous:
- `ACTION_PLAN.md` - Historical action plans
- `DOCUMENTATION_REVIEW.md` - Documentation reviews
- `PLACE_*.md` - Place schema migrations and validations
- `QA_*.md` - Various QA reports
- `QUALITY_*.md` - Quality check results
- `SCHEMA_*.md` - Schema compliance and migrations
- `SESSION_*.md` - Development session notes
- `STRUCTURED_OUTPUTS_*.md` - Structured outputs implementation
- `URL_EXTRACTION.md` - URL extraction documentation

---

## Consolidation History

### 2026-03-02: Archive Consolidation
- Merged `docs/archive` (42 files) and `docs/current/archived` (19 files)
- Organized into 6 subject-based directories
- Total: 61 archived files
- Removed duplicate directory structure

### Previous Locations
- **Old:** `docs/archive/` (flat structure, 42 files)
- **Old:** `docs/current/archived/` (flat structure, 19 files)
- **New:** `docs/archive/` (organized by subject, 61 files)

---

## Why Archived?

These documents were archived because they:
1. Have been superseded by newer documentation
2. Describe historical issues that have been resolved
3. Document deprecated features or approaches
4. Are no longer actively maintained
5. Provide historical context for feature evolution

---

## Accessing Current Documentation

For current documentation, see:
- **Main Index:** `../current/INDEX.md`
- **Core:** `../current/core/`
- **Pipeline:** `../current/pipeline/`
- **Features:** `../current/features/`
- **QA Reports:** `../current/qa-reports/`

---

## Retention Policy

Archived documentation is retained for:
- Historical reference
- Understanding feature evolution
- Debugging legacy issues
- Audit trail
- Learning from past decisions

These files may be reviewed annually and permanently deleted if no longer needed.

---

## File Count Summary

| Category | Files | Purpose |
|----------|-------|---------|
| Core | 5 | Architecture & API history |
| External Maps | 17 | Feature development history |
| People | 3 | People management history |
| Pipeline | 8 | Pipeline development history |
| QA Reports | 4 | Historical QA reports |
| Misc | 24 | Planning, reviews, schemas |
| **Total** | **61** | Complete historical record |
