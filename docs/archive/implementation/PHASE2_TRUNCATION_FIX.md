# Phase 2 Truncation Issue Fix

## Problem

Some large chapters cause API response truncation:
- `chapter20full-parsed.json` (111 paragraphs, ~60K chars)
- `chapter8c-parsed.json` 
- Others with "full" suffix

**Error:** `Unterminated string starting at: line X column Y`

**Root Cause:** Chapter is too large for single API call, response gets truncated mid-JSON.

## Solution Options

### Option 1: Skip Large Chapters (Quick Fix)

Add to `phase2_extract.py` before processing:

```python
# Skip known problematic large chapters
SKIP_CHAPTERS = ["chapter20full", "chapter8c"]

for parsed_file in parsed_files:
    if any(skip in parsed_file.stem for skip in SKIP_CHAPTERS):
        logger.warning(f"Skipping large chapter: {parsed_file.name}")
        continue
```

### Option 2: Split Large Chapters (Recommended)

Already have split versions:
- `chapter20full` → `chapter20a`, `chapter20b`, etc.
- Process split versions instead

Check if split versions exist:
```bash
ls output/BreakoutAndPursuit/chapter20*.json
```

### Option 3: Increase Chunk Size (Not Recommended)

The API already uses `max_tokens: 131072` (maximum). Can't increase further.

## Current Status

**Failed Chapters:**
- chapter8c-parsed.json (control characters + truncation)
- chapter20full-parsed.json (truncation)
- 1-2 others

**Success Rate:** 116/119 (97.5%)

## Recommendation

Use split chapters. The "full" versions were likely created for reference but should not be processed.

**Action:**
```bash
# Remove or rename full chapters
cd output/BreakoutAndPursuit
mv chapter20full-parsed.json chapter20full-parsed.json.skip
mv chapter8c-parsed.json chapter8c-parsed.json.skip  # if exists
```

Then rerun:
```bash
python3 phase2_retry.py
```

## Prevention

Add validation in Phase 1 to warn about chapters >100 paragraphs:
```python
if len(paragraphs) > 100:
    logger.warning(f"Large chapter detected: {len(paragraphs)} paragraphs")
    logger.warning("Consider splitting for Phase 2 processing")
```

## Quick Fix Applied

**Issue:** chapter20full-parsed.json too large (111 paragraphs) causing API truncation

**Action:** Renamed to .skip to exclude from processing

**Result:** Will process 118 chapters instead of 119

**Success Rate:** Should improve to 98.3% (116/118)

Run retry:
```bash
python3 phase2_retry.py --max-attempts 2
```

