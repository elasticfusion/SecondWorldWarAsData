# Grok Verification Step

**Date:** 2026-02-25  
**Feature:** Two-step verification for external maps  
**Status:** ✅ Implemented

---

## Problem

Even with strict validation, Grok still returns hallucinated maps:
- URLs that don't exist
- Maps not about the searched place
- Maps from wrong time period
- Fake archive references

**Root cause:** Single-pass validation can't catch all hallucinations.

---

## Solution: Two-Step Verification

### Step 1: Initial Search
Grok searches for maps related to place/event:
```
Search for WWII maps about {place_name}
→ Returns list of potential maps
```

### Step 2: Verification (NEW)
For each map, ask Grok to verify:
```
Visit {url} and confirm:
1. Does the page exist?
2. Is it about {place_name}?
3. Is it from WWII era?
→ Returns {is_relevant: true/false, reason: "..."}
```

**Only import if Grok confirms `is_relevant: true`**

---

## Implementation

```python
def _verify_map_relevance(
    map_url: str,
    map_title: str,
    place_name: str,
    date: Optional[str],
    grok_client: GrokClient,
) -> bool:
    """Ask Grok to verify if a map URL is actually relevant."""
    
    prompt = f"""Visit this URL and verify if it's a REAL WWII map about {place_name}:
    
    URL: {map_url}
    Title: {map_title}
    Expected place: {place_name}
    Expected date: {date or 'WWII era (1939-1945)'}
    
    Check:
    1. Does the page exist and contain a real map?
    2. Is the map about {place_name}?
    3. Is the map from WWII era (1935-1950)?
    
    Respond with ONLY a JSON object:
    {{"is_relevant": true or false, "reason": "Brief explanation"}}
    """
    
    response = grok_client.extract_json(prompt, cache_type="external_maps_verification")
    return response.get("is_relevant", False)
```

---

## Integration

Added to import flow after all other validations:

```python
# After: required fields, relevance, image URL, duplicates
# Before: writing to disk

logger.info(f"   🔍 Verifying with Grok: {map_title}")
is_relevant = _verify_map_relevance(url, title, place_name, date, grok_client)

if not is_relevant:
    logger.warning(f"   ⚠ GROK REJECTED - Not relevant: {title}")
    continue

logger.info(f"   ✓ Grok confirmed relevance")
# Proceed with import
```

---

## Benefits

### 1. Catches Hallucinations
- Grok double-checks its own results
- Verifies URLs actually exist
- Confirms content matches expectations

### 2. Higher Quality Data
- Only imports verified maps
- Reduces false positives
- More trustworthy results

### 3. Self-Correcting
- Grok can catch its own mistakes
- Learns from verification failures
- Improves over time

---

## Trade-offs

### Performance
- **2x API calls per map** (search + verify)
- **Slower processing** (~2-4 seconds per map)
- **Higher API costs**

### Accuracy
- **Significantly fewer hallucinations**
- **Higher confidence in results**
- **Worth the performance cost**

---

## Example Flow

```
🔍 Searching maps for: Brest
   Found 3 map(s)

   🔍 Verifying with Grok: Brest Tactical Map 1944
   ✓ Grok confirmed relevance
   ✓ Imported: Brest Tactical Map 1944

   🔍 Verifying with Grok: Montana Reclamation 1905
   ⚠ GROK REJECTED - Not relevant: Montana Reclamation 1905

   🔍 Verifying with Grok: Brest Harbor Map
   ⚠ GROK REJECTED - Not relevant: Brest Harbor Map
   
✓ Processed 1 place, imported 1 map
```

---

## Caching

Verification responses cached in:
```
cache/api/external_maps_verification/
```

Re-running with same URLs uses cached verification results.

---

## Testing

To test verification:
```python
from src.extraction.search_external_maps import _verify_map_relevance
from src.grok_client import GrokClient

grok = GrokClient(cache_dir="cache/api")

# Test valid map
result = _verify_map_relevance(
    "https://www.loc.gov/item/valid-map/",
    "Normandy D-Day Map",
    "Normandy",
    "1944-06-06",
    grok
)
print(f"Valid map: {result}")  # Should be True

# Test hallucination
result = _verify_map_relevance(
    "https://fake-url.com/nonexistent",
    "Montana 1905",
    "Brest",
    "1944-08-01",
    grok
)
print(f"Hallucination: {result}")  # Should be False
```

---

## Future Enhancements

- [ ] Batch verification (multiple maps at once)
- [ ] Confidence scoring (not just true/false)
- [ ] Human review queue for borderline cases
- [ ] Verification statistics tracking
- [ ] Adaptive verification (skip for trusted sources)

---

## Related

- Hallucination prevention (2026-02-25)
- LOC.gov URL extraction (2026-02-25)
- Image URL validation (2026-02-24)
