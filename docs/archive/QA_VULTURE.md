# Vulture (Dead Code) Analysis

**Date:** 2026-03-05  
**Tool:** Vulture - Dead code detection

---

## Analysis Results

### src/utils/file_lock.py

**Functions:**
- `write_json_with_lock()` - ✅ USED (dates.py, places.py, weather_central.py)
- `read_json_with_lock()` - ⚠️ UNUSED (defined but not imported anywhere)

**Decision:** KEEP
- Provides symmetric API with write function
- May be needed for future concurrent reads
- Minimal overhead (66 lines total for both functions)
- Good practice to have both read/write locks available

---

### src/extraction/concurrent.py

**All functions:** ✅ USED
- `extract_group1_concurrent()` → process_event_file_concurrent
- `extract_group2_concurrent()` → process_event_file_concurrent
- `extract_group3_sequential()` → process_event_file_concurrent
- `extract_group4_sequential()` → process_event_file_concurrent
- `process_event_file_concurrent()` → process_files_concurrent
- `process_files_concurrent()` → phase2_extract.py

**Verdict:** ✅ No dead code

---

### src/extraction/logistics.py

**Functions:**
- `_build_entity_index()` - ✅ USED (6 times)
- `_link_entities()` - ❌ UNUSED → **REMOVED**
- `_extract_logistics_with_llm()` - ✅ USED
- `_build_temporal()` - ✅ USED
- `_build_logistics_data()` - ✅ USED
- `extract_logistics_from_event()` - ✅ USED

**Action Taken:** Removed `_link_entities()` (entity linking done inline in `_build_logistics_data()`)

---

### Other Modified Files

- `src/grok_client.py` - ✅ No dead code
- `src/extraction/dates.py` - ✅ No dead code
- `src/extraction/places.py` - ✅ No dead code
- `src/extraction/weather_central.py` - ✅ No dead code

---

## Summary

| File | Dead Code | Action Taken |
|------|-----------|--------------|
| `file_lock.py` | `read_json_with_lock()` | Kept (future use) |
| `concurrent.py` | None | ✅ Clean |
| `logistics.py` | `_link_entities()` | ✅ Removed |
| Other files | None | ✅ Clean |

---

## Final Status

✅ **All dead code addressed**

- 1 unused function removed (logistics.py)
- 1 unused function kept for future use (file_lock.py)
- All other code is actively used

---

**Reviewed by:** Kiro AI  
**Status:** ✅ Clean (no actionable dead code remaining)

