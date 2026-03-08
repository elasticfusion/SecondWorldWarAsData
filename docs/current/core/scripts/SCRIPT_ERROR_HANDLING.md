# Script Error Handling Improvements

**Date:** 2026-03-05

---

## Issue

Scripts fail with unclear error messages when output directories don't exist or are empty.

**Example:**
```
ERROR:__main__:People groups directory not found: output/people_groups
```

User doesn't know what to do next.

---

## Improvements Made

### 1. find_related_groups.py

**Before:**
```python
if not groups_dir.exists():
    logger.error("People groups directory not found: %s", groups_dir)
    return 1
```

**After:**
```python
if not groups_dir.exists():
    logger.error("People groups directory not found: %s", groups_dir)
    logger.info("Run phase2_extract.py first to extract people groups")
    return 1

# Check if directory has any JSON files
group_files = list(groups_dir.glob("*.json"))
if not group_files:
    logger.error("No people group files found in: %s", groups_dir)
    logger.info("Run phase2_extract.py first to extract people groups")
    return 1

logger.info("Found %d people group file(s)", len(group_files))
```

### 2. find_duplicate_people.py

**Before:**
```python
if not people_dir.exists():
    logger.error("People directory not found: %s", people_dir)
    sys.exit(1)
```

**After:**
```python
if not people_dir.exists():
    logger.error("People directory not found: %s", people_dir)
    logger.info("Run phase2_extract.py first to extract people")
    sys.exit(1)

# Check if directory has any JSON files
people_files = [f for f in people_dir.glob("*.json") 
                if f.name not in ["index.json", "duplicate_report.json"]]
if not people_files:
    logger.error("No people files found in: %s", people_dir)
    logger.info("Run phase2_extract.py first to extract people")
    sys.exit(1)

logger.info("Found %d people file(s)", len(people_files))
```

---

## Benefits

1. **Clear Next Steps:** User knows to run phase2_extract.py
2. **Empty Directory Check:** Detects when directory exists but has no data
3. **File Count:** Shows how many files were found
4. **Better UX:** Helpful error messages instead of just errors

---

## Other Scripts

**Already Good:**
- `find_duplicate_places.py` - Has proper error handling
- `merge_*.py` scripts - Check for report files before merging

**No Changes Needed:**
- Merge scripts fail gracefully if no duplicates found
- Other utility scripts have adequate error handling

---

## Status

✅ **Improved error handling in find scripts**

Users will now get clear guidance when running scripts before data extraction is complete.
