# TODO: Fix Supplemental File Naming Collision

**Priority:** Medium  
**Created:** 2026-03-16

## Problem

Supplemental files write to flat `output/supplemental/` using chapter name only:
```
output/supplemental/chapter8c-endnotes.json
```

Both BreakoutAndPursuit and CrossChannelAttack have overlapping chapter names (chapter10a, chapter11a, etc.), so one book's files overwrite the other's.

## Affected Files

- `src/extraction/supplemental.py` → `_write_supplemental_files()` — uses `base_name` from event file without book prefix
- Potentially: weather, equipment, logistics, casualties if they write to flat directories

## Fix

Prepend book name to `base_name`:
```
chapter8c-endnotes.json → BreakoutAndPursuit-chapter8c-endnotes.json
```

Or use per-book subdirectories:
```
output/supplemental/BreakoutAndPursuit/chapter8c-endnotes.json
```

## Impact

- `_write_supplemental_files()` in supplemental.py
- `extract_supplemental()` caller passes book name or event file includes book path
- Check weather/equipment/logistics/casualties for same pattern
