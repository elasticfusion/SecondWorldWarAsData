# External Maps - Feature Summary

**Date:** 2026-02-24  
**Status:** Production Ready

---

## Core Features

### 1. Automated Search
- Processes all 220 places automatically
- No manual YAML curation required
- Grok searches web for WWII maps
- Rate limited (2s delay, 30 req/min)

### 2. Two-Step Verification (NEW)
- **Step 1:** Grok searches for maps
- **Step 2:** Grok verifies each URL before import
- Only imports if Grok confirms relevance
- Catches hallucinations that pass initial filters
- 2x API calls per map (worth it for quality)

### 3. Rich Context
Grok receives comprehensive context for each place:
- **Place info:** Name, current name, aliases
- **PlaceID:** Unique identifier
- **Date:** Historical date if available
- **Event context:** Event name and sub-event name
- **Event summary:** Overview from event file
- **Event paragraphs:** First 500 chars for details

### 4. Validation Filters

**Two-Step Verification:**
- Initial search returns candidate maps
- Each map verified by Grok before import
- Confirms: page exists, about correct place, WWII era
- Rejects if Grok says not relevant

**Relevance Check:**
- Place name must appear in title OR description
- Date must be 1935-1950 (WWII era) if place not mentioned
- Filters Grok hallucinations (e.g., Montana 1905 map for Brest)

**URL Validation:**
- HEAD request checks status code
- GET range request verifies content exists
- Content-type check rejects HTML error pages
- Filters broken/empty URLs

**Required Fields:**
- title
- external_source
- external_source_url

**Duplicate Detection:**
- Checks by external_source_url
- Prevents re-importing same map
- Idempotent operation

### 4. Error Handling
- Graceful degradation (continues on failure)
- Comprehensive logging (INFO/WARNING/ERROR)
- Null field handling with defaults
- Try-except blocks around all operations

---

## Search Scope

**Unrestricted:** Searches any source
- Official archives (NARA, IWM, LOC, etc.)
- Museums and historical societies
- University digital collections
- Historical map websites
- Any reputable source with WWII maps

---

## Output

Each map saved as JSON with:
```json
{
  "MapID": "01KJ...",
  "map_title": "Map title",
  "external_source": "Source name",
  "external_source_url": "https://...",
  "PlaceID": "01KJ...",
  "place_name": "Place name",
  "EventID": "01KJ...",
  "Event_Name": "Event name",
  "Sub_eventID": "01KJ...",
  "Sub_event_Name": "Sub-event name",
  "date": "1944-06-06",
  "license": "Public Domain",
  "found_via": "Grok search for Place",
  "found_date": "2026-02-24"
}
```

---

## Quality Metrics

**Code Quality:**
- Pylint: 9.50/10
- Mypy: No errors
- Bandit: 0 security issues
- Error handling: 11/11 patterns (100%)

**Expected Results:**
- Success rate: 10-30% of places
- Not all places have archival maps
- Validation filters ensure quality

---

## Integration

**Phase 2 Pipeline:**
- Runs automatically after places extraction
- Also imports from external_maps.yaml if exists
- Logs to phase2 log file

**Standalone:**
```bash
python3 -m src.extraction.search_external_maps
```

---

## Performance

**Time:** ~7-8 minutes for 220 places
- Rate limiting: 440 seconds (7.3 min)
- Grok processing: ~1-2 sec per request
- Validation: ~1 sec per map with image URL

**API Calls:** 220 (one per place)

**Cache:** Subsequent runs use cache (much faster)

---

## Validation Examples

**✅ ACCEPTED:**
```
Title: "Normandy Invasion - D-Day Beaches"
Place: Normandy ✓
Date: 1944-06-06 ✓
URL: Returns image content ✓
```

**❌ REJECTED:**
```
Title: "Montana Reclamation Project, 1905"
Place: Brest (not in title) ✗
Date: 1905 (outside WWII era) ✗
```

**❌ REJECTED:**
```
Title: "Brest Tactical Map"
URL: Returns HTML error page ✗
```

---

## Related Documentation

- **User Guide:** `docs/current/EXTERNAL_MAPS_AUTOMATED_SEARCH.md`
- **Changelog:** `docs/current/EXTERNAL_MAPS_CHANGELOG.md`
- **Specification:** `contextmanagement/Specs/external_maps.md`
- **Run Guide:** `docs/current/EXTERNAL_MAPS_RUN_GUIDE.md`
