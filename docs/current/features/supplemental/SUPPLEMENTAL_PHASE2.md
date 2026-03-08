# Phase 2 Implementation: Search Integration

## Overview

Phase 2 adds online resource search capabilities to supplemental material extraction, implementing the sequential search strategy with content verification.

## Implementation

### New File: `src/extraction/supplemental_search.py`

**Functions:**
1. `search_gutenberg()` - Search Gutenberg.org via OpenSERP
2. `search_archive_org()` - Search Archive.org Advanced Search API
3. `search_with_llm()` - Search using Grok LLM
4. `search_with_openserp()` - General web search via OpenSERP
5. `validate_url()` - Verify URL accessibility
6. `verify_url_content()` - **NEW:** Verify URL content matches citation using LLM
7. `sequential_search()` - Orchestrate search strategy
8. `enrich_materials_with_search()` - Main enrichment function

### Search Strategy

**Sequential Order:**
1. **Gutenberg.org** (if book/periodical) → Assumes public domain
2. **LLM Search** (Grok) → First pass for all materials
3. **Archive.org API** → Second pass if LLM fails
4. **OpenSERP** → Third pass if Archive.org fails

**Stops when:** Valid URL found, accessible, **and content verified**

### Content Verification (NEW)

**Purpose:** Prevent hallucinated or incorrect URLs

**How it works:**
1. URL found by search method
2. Check URL is accessible (HTTP 200)
3. **Use Grok LLM to verify URL content matches citation**
4. Only accept if title and author match

**Skips verification if:**
- No Grok client available
- Title is "Unknown" or empty (rejects immediately)
- No meaningful citation info

**Example:**
```
Citation: "The Rise and Fall of the Third Reich" by William L. Shirer
Found URL: https://archive.org/details/soundcloud-303013059
Verification: INVALID (SoundCloud audio, not the book)
Result: URL rejected, continue searching
```

### Configuration

Added to `config.yaml`:
```yaml
supplemental_material:
  enabled: false                   # Phase 1: Core extraction
  enrich_with_searches: false      # Phase 2: Enable search
  llm_search: true                 # Use LLM (first pass)
  search_gutenberg: false          # Search Gutenberg.org
  search_archive_org: false        # Search Archive.org
  use_openserp: false              # Use OpenSERP (third pass)
```

### Integration

Updated `phase2_extract.py`:
- Runs after Phase 1 extraction
- Only if `enrich_with_searches: true`
- Enriches existing supplemental files
- Logs enrichment results

## Search Metadata

Each material gets `search_metadata` object:
```json
{
  "gutenberg_checked": true,
  "gutenberg_url": "https://gutenberg.org/...",
  "archive_org_checked": true,
  "archive_org_url": "https://archive.org/details/...",
  "openserp_checked": false,
  "llm_search_checked": true,
  "found_via": "gutenberg",
  "search_date": "2026-03-07T16:50:00Z",
  "search_notes": null
}
```

## Features

### Gutenberg.org Search
- Only for books and periodicals
- Uses OpenSERP with `site:gutenberg.org` filter
- Assumes public domain for all results
- Stores URL in `gutenberg_url`
- **Content verified before acceptance**

### Archive.org Search
- Uses Advanced Search API
- Searches by author + title
- Returns first match
- Stores URL in `archive_org_url`
- **Content verified before acceptance**

### LLM Search
- Uses Grok API
- Caches results
- Returns URL or "NOT_FOUND"
- Validates URL format
- **Content verified before acceptance**

### OpenSERP Search
- General web search
- Returns first result
- Fallback option
- **Content verified before acceptance**

### URL Validation
- HEAD request to verify accessibility
- 10-second timeout
- Follows redirects
- Returns true if 200 OK

### Content Verification (NEW)
- **Uses Grok LLM to verify URL content**
- Checks title matches citation
- Checks author matches (if provided)
- Rejects URLs with wrong content
- Prevents hallucinated URLs
- Skips if title is "Unknown" or empty

## Usage

### Enable Phase 2

```yaml
# config.yaml
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true
```

### Run Pipeline

```bash
python3 phase2_extract.py
```

**Output:**
```
Extracting supplemental material...
  Extracted supplemental material
  Enriching with online searches...
  Searching for: The Rise and Fall of the Third Reich
    ✓ Found: https://archive.org/details/...
  Enriched 1 material(s)
```

### Standalone Enrichment

```python
from src.extraction.supplemental_search import enrich_materials_with_search
from src.grok_client import GrokClient

config = {
    "llm_search": True,
    "search_archive_org": True,
    "openserp_url": "http://localhost:7001",
}

grok_client = GrokClient("cache")

enriched = enrich_materials_with_search(
    supplemental_file=Path("output/supplemental/chapter1-supplemental.json"),
    config=config,
    grok_client=grok_client,
)

print(f"Enriched {enriched} materials")
```

## Data Updates

When URL found:
- `resource_urls` array populated
- `availability` changed to "online"
- `url_capture_date` set to current date
- `search_metadata` added with full details

When not found:
- `search_metadata` added with search attempts
- `search_notes` explains why not found
- Original data unchanged

## Error Handling

### Graceful Degradation
- Search failures don't stop enrichment
- Continues with next material
- Logs at DEBUG level

### Timeout Handling
- OpenSERP: 30 seconds
- Archive.org: 30 seconds
- URL validation: 10 seconds
- LLM: Uses Grok client timeout (360s)

### Exception Handling
- Subprocess errors (OpenSERP)
- HTTP errors (Archive.org, validation)
- JSON decode errors
- File I/O errors

## Quality Assurance

### QA Results
- **Pylint**: 9.94/10 ✅
- **Mypy**: 0 errors ✅
- **Bandit**: 0 issues ✅
- **Complexity**: C (18) for sequential_search - Acceptable
- **Maintainability**: A (46.11) ✅

### Complexity Justification
- `sequential_search`: C (18) - Multiple search passes with validation
- `enrich_materials_with_search`: C (14) - File I/O and iteration
- Both acceptable per QA spec for search/enrichment logic

## Performance

### API Calls
- LLM search: 1 call per material (cached)
- Archive.org: 1 call per material
- URL validation: 1 call per found URL
- **Content verification: 1 LLM call per found URL (NEW)**
- OpenSERP: Subprocess call (no API limit)

### Timing
- ~2-5 seconds per material (with searches)
- **~2-3 seconds additional for content verification**
- ~10-25 seconds per chapter (5-10 materials)
- Cached LLM responses instant

### Cost
- LLM search: ~$0.001 per material
- **Content verification: ~$0.001 per URL (NEW)**
- Archive.org: Free
- OpenSERP: Free (local service)
- URL validation: Free
- **Total: ~$0.002-0.005 per material**

## Dependencies

All already in `requirements.txt`:
- httpx (HTTP client)
- subprocess (OpenSERP calls)
- json, datetime (standard library)

## Testing

### Manual Test

```bash
# 1. Enable Phase 2
# Edit config.yaml: enrich_with_searches: true

# 2. Run on existing supplemental file
python3 -c "
from pathlib import Path
from src.extraction.supplemental_search import enrich_materials_with_search
from src.grok_client import GrokClient

config = {'llm_search': True, 'search_archive_org': True}
grok = GrokClient(Path('cache'))

enriched = enrich_materials_with_search(
    Path('output/supplemental/Breakout_and_Pursuit/chapter1-supplemental.json'),
    config,
    grok
)
print(f'Enriched: {enriched}')
"
```

### Expected Behavior
- Searches for each material without URLs
- Logs search attempts
- Updates file with search metadata
- Reports enrichment count

## Limitations

### Phase 2 Limitations
- No ISBN extraction (Phase 3)
- No copyright determination (Phase 3)
- No archive URL verification (Phase 3)
- No author death date lookup (Phase 3)

### Search Limitations
- LLM may hallucinate URLs (content verification catches most)
- Archive.org may not have all materials
- OpenSERP requires local service
- Gutenberg limited to public domain works
- **Citations with "Unknown" title are skipped**
- **Empty or incomplete citations cannot be searched**

### Known Issues
- **Incomplete endnotes:** If source markdown has endnote references `[50]` but no actual citation text, extraction creates empty citation with title "Unknown"
- **Content verification requires LLM:** Adds ~2-3 seconds per URL check
- **False negatives possible:** LLM may incorrectly reject valid URLs (rare)

## Next Steps

### Phase 3: Advanced Features
1. ISBN extraction for books
2. Copyright determination
3. Author death date lookup
4. Archive URL verification
5. Enhanced license detection

## Related Files

- `src/extraction/supplemental.py` - Phase 1 core extraction
- `src/extraction/supplemental_search.py` - Phase 2 search integration
- `contextmanagement/Specs/supplementalmaterial_v2.md` - Full specification
- `config.yaml` - Configuration
- `phase2_extract.py` - Pipeline integration

## Status

✅ **Phase 2 Complete**
- All search methods implemented
- Sequential strategy working
- URL validation in place
- Search metadata tracked
- QA passed
- Integrated into pipeline
