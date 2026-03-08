# CRITICAL ISSUE: Grok Verification Failure

**Date:** 2026-02-25  
**Severity:** 🔴 CRITICAL  
**Status:** ⚠️ BROKEN - DO NOT USE

---

## Problem

All 3 imported external maps are **complete hallucinations**:

| MapID | Claimed Content | Actual Content at URL |
|-------|----------------|----------------------|
| 01KJBD1FGKMEPEPZ9JN52VR352 | "South Pacific Ocean WWII map (1944)" | "Catfishing on Ottawa River (2000)" |
| 01KJBD7QRD3C2DV5J6VDZGBB80 | "European Theater lines of communications (1944)" | "2008 Wyoming bighorn sheep areas" |
| 01KJBD8020A1SD3ZC98AGJRQ4A | "European Theater organization chart (1945)" | "2008 Wyoming bighorn sheep areas" |

**None of these are WWII maps. None are even from the correct time period.**

---

## Root Cause

### Grok Cannot Visit URLs

The verification function `_verify_map_relevance()` asks Grok to:
```
Visit this URL and verify if it's a REAL WWII map...
```

**But Grok cannot actually visit URLs.** It's an LLM, not a web browser.

### What's Actually Happening

1. Grok searches and returns map titles/URLs (hallucinated)
2. Verification asks Grok to "visit" the URL
3. Grok **makes up** what it thinks should be there based on the title
4. Grok confirms `is_relevant: true` based on fabricated content
5. Hallucinated map gets imported

### Why Other Validations Failed

1. **`_check_relevance()`** - Only checks title/description (which are hallucinated)
2. **`_validate_image_url()`** - Extracted wrong image URLs that happen to exist
3. **`_is_photograph()`** - Title doesn't contain photo keywords
4. **Date validation** - Hallucinated dates are in valid range (1944-1945)

---

## Impact

**100% false positive rate** - All imported maps are hallucinations.

The feature is **completely broken** and should not be used in production.

---

## Why This Wasn't Caught Earlier

1. **No manual verification** of actual URLs during testing
2. **Trusted Grok verification** without validating it works
3. **Image URL extraction** succeeded (but extracted wrong images)
4. **All validations passed** because they only check metadata, not actual content

---

## Required Fixes

### Option 1: Remove Grok Verification (Recommended)

Grok verification **cannot work** because LLMs can't visit URLs.

**Replace with:**
```python
def _verify_url_content(url: str, expected_keywords: List[str]) -> bool:
    """Fetch URL and check if HTML contains expected keywords."""
    response = httpx.get(url, timeout=10)
    html = response.text.lower()
    
    # Check if page contains expected keywords
    return any(keyword.lower() in html for keyword in expected_keywords)
```

### Option 2: Use Archive APIs

Instead of asking Grok to search, use official APIs:
- Library of Congress API: `https://www.loc.gov/apis/`
- NARA API: `https://catalog.archives.gov/api/v1/`
- Imperial War Museum API

### Option 3: Manual Curation Only

Disable automated search entirely. Only import from manually curated `external_maps.yaml`.

---

## Immediate Actions

1. **Delete all external maps:**
   ```bash
   rm output/external_maps/*.json
   ```

2. **Disable automated search:**
   ```yaml
   # config.yaml
   external_maps:
     enabled: false
   ```

3. **Remove verification function** from `search_external_maps.py`

4. **Update documentation** to warn about this issue

---

## Long-Term Solution

### Hybrid Approach

1. **Grok suggests search queries** (not URLs)
2. **Use archive APIs** to search with those queries
3. **Validate returned URLs** by fetching and parsing HTML
4. **Extract metadata** from actual page content
5. **Human review** before import

### Example Flow

```python
# Step 1: Grok suggests search terms
search_terms = grok.suggest_search_terms(place_name, date, event)
# Returns: ["Normandy D-Day 1944", "Operation Overlord maps"]

# Step 2: Search LOC API
results = loc_api.search(search_terms[0])

# Step 3: Validate each result
for result in results:
    html = fetch_url(result.url)
    if validate_content(html, place_name, date):
        import_map(result)
```

---

## Testing Requirements

Before re-enabling:

1. **Manual verification** - Check 10 random URLs actually contain claimed content
2. **Content validation** - Parse HTML to verify keywords present
3. **Date validation** - Extract date from page, not from Grok
4. **Image validation** - Verify extracted images are actually maps
5. **Human review** - All imports reviewed before committing

---

## Status

🔴 **CRITICAL - DO NOT USE**

The external maps automated search feature is **completely broken** and produces 100% hallucinations.

**Recommended action:** Disable feature until proper URL validation is implemented.

---

## Related Files

- `src/extraction/search_external_maps.py` - Broken verification
- `docs/current/GROK_VERIFICATION.md` - Documents broken approach
- `output/external_maps/*.json` - All hallucinations, should be deleted
