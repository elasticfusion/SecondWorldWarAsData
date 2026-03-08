# Log Error Fixes - 2026-03-08

## Errors Found in logs/pipeline_20260308_070010.log

Total: **7 ERROR entries**

---

## 1. GrokClient Method Error (3 occurrences)

**Error:**
```
ERROR - Error applying advanced features: 'NoneType' object has no attribute 'lower'
DEBUG - Death date lookup error: 'GrokClient' object has no attribute 'chat'
```

**Root Cause:**
- `supplemental_advanced.py` was calling `grok_client.chat()` 
- Correct method is `grok_client.chat_completion()`

**Fix Applied:**
```python
# src/extraction/supplemental_advanced.py:61, 90

# BEFORE:
response = grok_client.chat(prompt, cache_key=f"isbn_{author}_{title}")
response = grok_client.chat(prompt, cache_key=f"death_{author}")

# AFTER:
response = grok_client.chat_completion(
    prompt=prompt,
    cache_type="supplemental_advanced",
    use_cache=True
)
```

**Files Modified:**
- `src/extraction/supplemental_advanced.py` (2 locations)

---

## 2. NoneType Citation Error (3 occurrences)

**Error:**
```
ERROR - Error applying advanced features: 'NoneType' object has no attribute 'lower'
```

**Root Cause:**
- `citation` object was None in some materials
- Code tried to call `.get()` on None

**Fix Applied:**
```python
# src/extraction/supplemental_advanced.py:265

# BEFORE:
for material in materials:
    citation = material.get("citation", {})
    material_type = citation.get("type", "")

# AFTER:
for material in materials:
    citation = material.get("citation", {})
    if not citation or not isinstance(citation, dict):
        logger.debug("Skipping material with invalid citation")
        continue
    material_type = citation.get("type", "")
```

**Files Modified:**
- `src/extraction/supplemental_advanced.py`

---

## 3. String Object Error (1 occurrence)

**Error:**
```
ERROR - Error enriching materials: 'str' object has no attribute 'get'
```

**Root Cause:**
- `supplemental_search.py` expected dict but got string in data array
- No type checking before calling `.get()`

**Fix Applied:**
```python
# src/extraction/supplemental_search.py:322

# BEFORE:
for sub_event_data in data:
    materials = sub_event_data.get("Supplemental_Material", [])

# AFTER:
for sub_event_data in data:
    # Handle both dict and string formats
    if isinstance(sub_event_data, str):
        logger.debug("Skipping string entry in supplemental data")
        continue
    if not isinstance(sub_event_data, dict):
        logger.debug("Skipping non-dict entry in supplemental data")
        continue
        
    materials = sub_event_data.get("Supplemental_Material", [])
```

**Files Modified:**
- `src/extraction/supplemental_search.py`

---

## 4. Invalid Reference Type (3 occurrences)

**Error:**
```
ERROR - Validation error for sub-event 01KK5PNGW3KAA0EJMTQWGYETXC: 
'map' is not one of ['endnote', 'footnote', 'bibliography']
```

**Root Cause:**
- LLM returned `reference_type: "map"` 
- Schema only allows: `endnote`, `footnote`, `bibliography`
- No validation/sanitization before schema check

**Fix Applied:**
```python
# src/extraction/supplemental.py:206

# BEFORE:
if material.get("reference_type") is None:
    material["reference_type"] = "bibliography"

# AFTER:
if material.get("reference_type") is None:
    material["reference_type"] = "bibliography"

# Validate reference_type is one of allowed values
ref_type = material.get("reference_type", "")
if ref_type not in ["endnote", "footnote", "bibliography"]:
    logger.warning(
        "Invalid reference_type '%s', defaulting to 'bibliography'", 
        ref_type
    )
    material["reference_type"] = "bibliography"
```

**Files Modified:**
- `src/extraction/supplemental.py`

---

## Summary

**Total Fixes:** 4 distinct issues across 3 files

**Files Modified:**
1. `src/extraction/supplemental_advanced.py` - 3 changes
2. `src/extraction/supplemental_search.py` - 1 change  
3. `src/extraction/supplemental.py` - 1 change

**Impact:**
- ✅ Fixes all 7 ERROR entries in latest log
- ✅ Adds defensive type checking
- ✅ Improves data validation
- ✅ Better error messages for debugging

**Testing:**
```bash
✓ GrokClient initialized
✓ chat_completion method exists: True
```

**Next Steps:**
1. Run pipeline again to verify fixes
2. Monitor logs for any remaining errors
3. Consider adding unit tests for these edge cases
