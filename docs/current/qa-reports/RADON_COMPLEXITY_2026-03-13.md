# Radon Complexity Report - C Grade or Worse

**Date:** 2026-03-13  
**Threshold:** C (11-20) or worse  
**Total Functions:** 42

---

## Grade D (21-30) - Very High Complexity ⚠️

### Critical Priority

1. **src/extraction/openserp_maps.py::import_openserp_maps** - D (28)
   - Import and process OpenSERP map search results
   - **Recommendation:** Break into smaller functions for validation, deduplication, and import

2. **src/extraction/openserp_maps.py::_check_license_terms** - D (25)
   - Check license terms in map metadata
   - **Recommendation:** Extract license checking logic into separate validators

3. **src/discovery.py::discover_content_structure** - D (24)
   - Discover content repository structure
   - **Recommendation:** Split into directory scanning and metadata extraction functions

4. **src/extraction/supplemental_advanced.py::enrich_with_advanced_features** - D (23)
   - Enrich supplemental materials with advanced features
   - **Recommendation:** Extract ISBN, copyright, and archive verification into separate functions

5. **src/extraction/supplemental_advanced.py::determine_copyright_status** - D (22)
   - Determine copyright status of materials
   - **Recommendation:** Break into year calculation, author lookup, and status determination

---

## Grade C (11-20) - Moderate Complexity

### High Priority (16-20)

6. **src/extraction/equipment.py::_download_media_file** - C (20)
7. **src/extraction/supplemental_info_pipeline.py::extract_from_supplemental_info** - C (17)
8. **src/extraction/equipment.py::merge_or_create_equipment** - C (16)
9. **src/extraction/weather_central.py::_find_or_create_weather** - C (16)

### Medium Priority (14-15)

10. **src/grok_client.py::GrokClient.extract_json** - C (15)
11. **src/utils/json_validator.py::validate_json** - C (15)
12. **src/extraction/weather_central.py::extract_weather_central** - C (15)
13. **src/grok_client.py::GrokClient._call_api** - C (14)
14. **src/extraction/people_consolidation.py::consolidate_people** - C (14)
15. **src/extraction/places.py::extract_places** - C (14)
16. **src/extraction/people.py::extract_people** - C (14)
17. **src/extraction/dates.py::extract_dates** - C (14)
18. **src/extraction/weather_central.py::_lookup_coordinates** - C (14)

### Lower Priority (11-13)

19. **src/extraction/equipment.py::_enrich_and_add_media** - C (13)
20. **src/extraction/places.py::_process_place_mention** - C (13)
21. **src/extraction/events.py::extract_events** - C (13)
22. **src/extraction/supplemental_advanced.py::extract_isbn** - C (13)
23. **src/grok_client.py::GrokClient.extract_json_with_image_base64** - C (12)
24. **src/parser.py::parse_metadata** - C (12)
25. **src/extraction/equipment.py::_extract_media_with_openserp** - C (12)
26. **src/extraction/maps.py::_process_event_files** - C (12)
27. **src/extraction/places.py::_fix_null_fields** - C (12)
28. **src/extraction/search_external_maps.py::_verify_map_relevance** - C (12)
29. **src/extraction/external_maps.py::find_event_from_place** - C (12)
30. **src/extraction/weather_central.py::_filter_invalid_weather** - C (12)
31. **src/parser.py::parse_content_file** - C (11)
32. **src/utils/validation_reports.py::generate_trend_report** - C (11)
33. **src/extraction/batch_parallel.py::process_chapters_parallel** - C (11)
34. **src/extraction/maps.py::_download_map_image** - C (11)
35. **src/extraction/places.py::_fix_invalid_ulids** - C (11)
36. **src/extraction/enrich_biographies.py::search_wikipedia** - C (11)
37. **src/extraction/supplemental.py::_separate_by_type** - C (11)
38. **src/extraction/weather_central.py::_fix_invalid_ulids** - C (11)
39. **src/extraction/weather_central.py::create_weather_prompt** - C (11)
40. **src/extraction/dates.py::_fix_invalid_ulids** - C (11)

---

## Summary by Grade

| Grade | Complexity | Count | Percentage |
|-------|------------|-------|------------|
| D | 21-30 | 5 | 11.9% |
| C | 11-20 | 37 | 88.1% |
| **Total** | **≥11** | **42** | **100%** |

---

## Summary by File

| File | Functions | Worst |
|------|-----------|-------|
| src/extraction/openserp_maps.py | 2 | D (28) |
| src/extraction/supplemental_advanced.py | 3 | D (23) |
| src/extraction/weather_central.py | 6 | C (16) |
| src/extraction/equipment.py | 4 | C (20) |
| src/extraction/places.py | 4 | C (14) |
| src/grok_client.py | 3 | C (15) |
| src/extraction/dates.py | 2 | C (14) |
| src/extraction/maps.py | 2 | C (12) |
| src/parser.py | 2 | C (12) |
| src/discovery.py | 1 | D (24) |
| src/utils/json_validator.py | 1 | C (15) |
| src/utils/validation_reports.py | 1 | C (11) |
| src/extraction/people_consolidation.py | 1 | C (14) |
| src/extraction/batch_parallel.py | 1 | C (11) |
| src/extraction/events.py | 1 | C (13) |
| src/extraction/search_external_maps.py | 1 | C (12) |
| src/extraction/people.py | 1 | C (14) |
| src/extraction/supplemental_info_pipeline.py | 1 | C (17) |
| src/extraction/enrich_biographies.py | 1 | C (11) |
| src/extraction/external_maps.py | 1 | C (12) |
| src/extraction/supplemental.py | 1 | C (11) |

---

## Refactoring Recommendations

### Immediate (D Grade)
1. **openserp_maps.py** - Split import_openserp_maps and _check_license_terms
2. **discovery.py** - Split discover_content_structure
3. **supplemental_advanced.py** - Extract copyright and enrichment logic

### Short-term (C Grade 16-20)
4. **equipment.py** - Simplify media download and merge logic
5. **supplemental_info_pipeline.py** - Extract entity extraction into separate functions
6. **weather_central.py** - Simplify weather lookup and creation

### Long-term (C Grade 11-15)
7. **grok_client.py** - Simplify JSON extraction and API call logic
8. **Extraction modules** - Extract common patterns (ULID fixing, validation)
9. **Parser modules** - Simplify metadata and content parsing

---

## Common Patterns to Extract

1. **ULID Fixing** - Appears in 3 files (dates, places, weather_central)
   - Create shared `fix_invalid_ulids()` utility

2. **Validation Logic** - Scattered across extraction modules
   - Create shared validation utilities

3. **API Retry Logic** - Duplicated in multiple extractors
   - Already exists in grok_client, ensure consistent use

4. **File I/O Patterns** - Similar read/write patterns
   - Create shared file utilities

---

**Generated:** 2026-03-13  
**Tool:** Radon CC  
**Command:** `python3 -m radon cc src/ -s -n C`
