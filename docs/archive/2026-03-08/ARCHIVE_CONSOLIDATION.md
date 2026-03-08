# Archive Consolidation

**Date:** 2026-03-02  
**Status:** Complete

---

## Summary

Consolidated two separate archive directories into one organized structure with subject-based folders.

---

## Changes Made

### Before
```
docs/
├── archive/           # 42 files (flat structure)
└── current/
    └── archived/      # 19 files (flat structure)
```

### After
```
docs/
├── archive/           # 61 files (organized by subject)
│   ├── core/         # 5 files
│   ├── external-maps/# 17 files
│   ├── people/       # 3 files
│   ├── pipeline/     # 8 files
│   ├── qa-reports/   # 4 files
│   └── misc/         # 24 files
└── current/          # Active documentation
```

---

## Files Consolidated

### From `docs/archive/` (42 files)
Categorized into:
- **Core:** 5 files (cache, API, metadata)
- **People:** 3 files (management, integration)
- **Pipeline:** 8 files (phase 1/2, fixes)
- **QA Reports:** 2 files (code quality)
- **Misc:** 24 files (planning, schemas, reviews)

### From `docs/current/archived/` (19 files)
Categorized into:
- **External Maps:** 17 files (all external maps history)
- **QA Reports:** 2 files (external maps QA)

---

## Organization by Subject

### Core (5 files)
Architecture, API, and cache documentation:
- Cache review and structure
- Grok API flow
- Place extraction issues
- Old metadata system

### External Maps (17 files)
Complete external maps feature history:
- Implementation and features
- Guides and fixes
- Anti-hallucination strategy
- Verification and whitelist
- Changelogs

### People (3 files)
People management evolution:
- Central management approach
- Integration completion
- Single file per person

### Pipeline (8 files)
Pipeline development history:
- Phase 1 and 2 completion
- Duplicate fixes
- Map URL fixes
- Date extraction

### QA Reports (4 files)
Historical quality assurance:
- Code quality reports
- External maps QA reports

### Misc (24 files)
Planning, reviews, and schemas:
- Action plans
- Documentation reviews
- Place schema migrations
- QA and quality reports
- Session notes
- Structured outputs

---

## Benefits

### Improved Organization
- Subject-based folders instead of flat structure
- Related documents grouped together
- Easier to find historical context

### Reduced Duplication
- Single archive location
- No confusion between two archive directories
- Clear separation from current docs

### Better Maintainability
- Organized by domain
- Clear categorization
- Easier to review and prune

### Scalability
- Easy to add new archived docs
- Clear conventions for categorization
- Structured for growth

---

## File Count Summary

| Location | Before | After | Change |
|----------|--------|-------|--------|
| docs/archive/ | 42 | 61 | Consolidated |
| docs/current/archived/ | 19 | 0 | Moved |
| **Total Archived** | **61** | **61** | Organized |

---

## Documentation Updated

- **archive/README.md** - Comprehensive guide to archived docs
- **current/INDEX.md** - Updated to reflect single archive location

---

## Migration Notes

### Breaking Changes
- `docs/current/archived/` no longer exists
- All archived docs now in `docs/archive/` with subject folders

### Path Updates
Old paths like:
- `docs/archive/PHASE1_COMPLETE.md`
- `docs/current/archived/EXTERNAL_MAPS_CHANGELOG.md`

New paths:
- `docs/archive/pipeline/PHASE1_COMPLETE.md`
- `docs/archive/external-maps/EXTERNAL_MAPS_CHANGELOG.md`

---

## Next Steps

1. ✅ Archives consolidated
2. ✅ Organized by subject
3. ✅ README created
4. Update any references to old archive paths (if any)
5. Consider annual review for permanent deletion of very old docs

---

## Verification

```bash
# Check structure
tree docs/archive -L 1

# Verify file counts
find docs/archive/core -name "*.md" | wc -l        # 5
find docs/archive/external-maps -name "*.md" | wc -l  # 17
find docs/archive/people -name "*.md" | wc -l      # 3
find docs/archive/pipeline -name "*.md" | wc -l    # 8
find docs/archive/qa-reports -name "*.md" | wc -l  # 4
find docs/archive/misc -name "*.md" | wc -l        # 24
```

Total: 61 archived files organized by subject matter.
