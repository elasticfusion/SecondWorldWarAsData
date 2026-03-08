# Supplemental Material Extraction - Complete Implementation

## Overview

Complete 3-phase implementation for extracting, enriching, and tracking referenced materials (footnotes, endnotes, bibliography) from WWII historical documents.

## Implementation Status

### ✅ Phase 1: Core Extraction (Complete)
**File:** `src/extraction/supplemental.py` (249 lines)

**Features:**
- ULID generation for MaterialID, EventID, Sub-eventID linkage
- Structured citation parsing (author, title, publisher, dates, ISBN, etc.)
- Reference type classification (endnote/footnote/bibliography)
- Availability determination (online/offline/archive/unknown)
- Basic license detection (public_domain/copyright/unknown)
- ISO 3166-1 alpha-3 country codes
- JSON schema validation before file write
- Retry logic with exponential backoff (3 attempts)
- Graceful degradation
- **Narrative extraction from footnotes/endnotes (NEW)**
- **Appends narrative content as sub-events to chapter files**
- **Duplicate prevention for multiple runs**

**Narrative Extraction:**
- Detects footnotes/endnotes with historical narrative (beyond citations)
- Creates new sub-events from narrative content
- Appends to both chapter*-event.json and chapter*-parsed.json
- Tracks by `reference_source` field to prevent duplicates
- Example: "Footnote 4 notes that German 7th Army was depleted..." → New sub-event

**QA Results:**
- Pylint: 10.00/10 ✅
- Mypy: 0 errors ✅
- Bandit: 0 security issues ✅
- Complexity: C (11) - Acceptable
- Maintainability: A (52.22) ✅

### ✅ Phase 2: Search Integration (Complete)
**File:** `src/extraction/supplemental_search.py` (243 lines)

**Features:**
- Sequential search strategy (LLM → Archive.org → OpenSERP)
- Gutenberg.org search for books/periodicals (via OpenSERP)
- Archive.org Advanced Search API integration
- LLM search using Grok API (cached)
- OpenSERP web search fallback
- URL validation (HEAD request)
- **Content verification using LLM (prevents hallucinations)**
- Search metadata tracking
- **Skips empty/unknown citations**

**Search Strategy:**
1. Gutenberg.org (books/periodicals only) → Public domain
2. LLM Search (Grok) → First pass
3. Archive.org API → Second pass
4. OpenSERP → Third pass
5. Stop when valid URL found **and content verified**

**Content Verification:**
- Uses Grok to verify URL matches citation
- Checks title and author
- Rejects incorrect URLs (e.g., SoundCloud when expecting book)
- Prevents hallucinated results

**QA Results:**
- Pylint: 9.94/10 ✅
- Mypy: 0 errors ✅
- Bandit: 0 security issues ✅
- Complexity: C (18) - Acceptable
- Maintainability: A (46.11) ✅

### ✅ Phase 3: Advanced Features (Complete)
**File:** `src/extraction/supplemental_advanced.py` (225 lines)

**Features:**
- ISBN extraction for books (post-1966)
- Author death date lookup via LLM
- Copyright determination (USA, EU, UK)
- Archive URL verification

**Copyright Rules:**
- USA: Pre-1928 (public domain), 1928-1977 (95 years), Post-1977 (Life+70)
- EU/UK: Life + 70 years
- Government works: Public domain

**QA Results:**
- Pylint: 9.81/10 ✅
- Mypy: 0 errors ✅
- Bandit: 0 security issues ✅
- Complexity: D (21) - Acceptable for copyright logic
- Maintainability: A (43.28) ✅

## Configuration

```yaml
supplemental_material:
  # Phase 1: Core extraction
  enabled: false                   # Enable supplemental material extraction
  extract_citations: true          # Parse citations into structured format
  max_materials_per_chapter: 100   # Limit materials per chapter
  
  # Phase 2: Search integration
  enrich_with_searches: false      # Enable online resource searches
  llm_search: true                 # Use LLM for search (first pass)
  search_gutenberg: false          # Search Gutenberg.org for books/periodicals
  search_archive_org: false        # Search Archive.org (second pass)
  use_openserp: false              # Use OpenSERP for web search (third pass)
  openserp_url: "http://localhost:7001"
  
  # Phase 3: Advanced features
  extract_isbn: false              # Extract ISBN for books (post-1966)
  determine_copyright: false       # Determine copyright status
  verify_archive_urls: false       # Verify archive URLs are accessible
```

## Usage

### Basic Usage (Phase 1 Only)

```yaml
# config.yaml
supplemental_material:
  enabled: true
```

```bash
python3 phase2_extract.py
```

### Full Pipeline (All Phases)

```yaml
# config.yaml
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true
```

```bash
python3 phase2_extract.py
```

### Standalone Usage

```python
from pathlib import Path
from src.extraction.supplemental import extract_supplemental
from src.extraction.supplemental_search import enrich_materials_with_search
from src.extraction.supplemental_advanced import enrich_with_advanced_features
from src.grok_client import GrokClient

# Phase 1: Extract
supplemental_file = extract_supplemental(
    event_file=Path("output/Breakout_and_Pursuit/chapter1-event.json"),
    grok_client=GrokClient(Path("cache")),
    output_dir=Path("output/supplemental"),
)

# Phase 2: Search
config = {"llm_search": True, "search_archive_org": True}
enrich_materials_with_search(supplemental_file, config, grok_client)

# Phase 3: Advanced
config = {"extract_isbn": True, "determine_copyright": True}
enrich_with_advanced_features(supplemental_file, config, grok_client)
```

## Data Structure

### Complete Material Example

```json
{
  "MaterialID": "01HQXYZ123...",
  "EventID": "01HQABC456...",
  "Sub-eventID": "01HQDEF789...",
  "reference_type": "bibliography",
  "reference_number": null,
  "verbatim_reference": "Shirer, William L. The Rise and Fall of the Third Reich. New York: Simon & Schuster, 1960.",
  
  "citation": {
    "author": "William L. Shirer",
    "title": "The Rise and Fall of the Third Reich",
    "publisher": "Simon & Schuster",
    "publication_date": "1960-01-01",
    "publication_location": "New York",
    "publication_country": "USA",
    "isbn": "0671728695",
    "type": "book"
  },
  
  "availability": "online",
  "resource_urls": ["https://archive.org/details/risefallofsthird00shir"],
  "url_capture_date": "2026-03-07T16:54:00Z",
  "license": "copyright",
  
  "search_metadata": {
    "gutenberg_checked": false,
    "archive_org_checked": true,
    "archive_org_url": "https://archive.org/details/risefallofsthird00shir",
    "llm_search_checked": true,
    "openserp_checked": false,
    "found_via": "archive_org",
    "search_date": "2026-03-07T16:54:00Z"
  },
  
  "copyright_status": {
    "status": "copyright",
    "author_death_date": "1993-12-28",
    "determination_basis": "Under copyright until 2063",
    "jurisdiction": "USA"
  }
}
```

## Processing Flow

### Phase 1: Core Extraction
1. Load event file
2. Extract LLM to identify citations
3. Parse citation components
4. Classify reference type
5. Determine availability
6. Generate ULIDs
7. Validate against JSON schema
8. Write to file

### Phase 2: Search Integration
1. Load supplemental file
2. For each material without URLs:
   - Try Gutenberg.org (books/periodicals)
   - Try LLM search
   - Try Archive.org API
   - Try OpenSERP
   - Validate URL
   - Stop when found
3. Update search metadata
4. Write updated file

### Phase 3: Advanced Features
1. Load supplemental file
2. For each material:
   - Extract ISBN (if book, post-1966)
   - Lookup author death date
   - Determine copyright status
   - Verify archive URLs
3. Update copyright metadata
4. Write updated file

## Performance

### Timing
- Phase 1: ~5-10 seconds per chapter
- Phase 2: ~10-20 seconds per chapter (with searches)
- Phase 3: ~5-10 seconds per chapter (with LLM calls)
- Total: ~20-40 seconds per chapter (all phases)

### API Calls
- Phase 1: 1 LLM call per chapter
- Phase 2: 1-3 calls per material (LLM, Archive.org, validation)
- Phase 3: 1-2 LLM calls per material (ISBN, death date)

### Cost
- Phase 1: ~$0.01 per chapter
- Phase 2: ~$0.001-0.003 per material
- Phase 3: ~$0.002-0.005 per material
- Total: ~$0.01-0.05 per chapter

## Error Handling

### Graceful Degradation
- Phase 1 failure → No supplemental file created
- Phase 2 failure → Original data preserved
- Phase 3 failure → Original data preserved
- Individual material failures → Continue with next

### Retry Logic
- Phase 1: 3 retries with exponential backoff
- Phase 2: No retries (searches are idempotent)
- Phase 3: No retries (LLM calls are cached)

### Timeout Handling
- LLM calls: 360 seconds (Grok client default)
- Archive.org: 30 seconds
- URL validation: 10 seconds
- OpenSERP: 30 seconds

## Quality Assurance

### All Phases Pass QA
- **Pylint**: 9.81-10.00/10 ✅
- **Mypy**: 0 errors ✅
- **Bandit**: 0 security issues ✅
- **Complexity**: Acceptable (C-D for complex logic)
- **Maintainability**: A (43-52) ✅

### Code Standards
- Lazy % formatting for logging
- Type hints throughout
- JSON schema validation
- Error handling patterns from `error_handling.md`
- QA standards from `quality_assurance.md`

## Documentation

### Specification
- `contextmanagement/Specs/supplementalmaterial_v2.md` - Full specification
- `contextmanagement/Specs/supplementalmaterial_v2.json` - JSON schema

### Implementation Docs
- `docs/current/SUPPLEMENTAL_PHASE1.md` - Phase 1 details
- `docs/current/SUPPLEMENTAL_PHASE2.md` - Phase 2 details
- `docs/current/PHASE3_COMPLETE.md` - Phase 3 details
- `docs/current/SUPPLEMENTAL_QA_RESULTS.md` - QA results
- `docs/current/SUPPLEMENTAL_ERROR_HANDLING.md` - Error handling
- `docs/current/SUPPLEMENTAL_VALIDATION.md` - Validation details
- `docs/current/PHASE1_IMPLEMENTATION_SUMMARY.md` - Original summary

### This Document
- Complete overview of all phases
- Configuration guide
- Usage examples
- Performance metrics

## Dependencies

All in `requirements.txt`:
- httpx - HTTP client
- ulid-py - ULID generation
- jsonschema - JSON validation
- subprocess - OpenSERP integration
- Standard library: json, datetime, re, logging, pathlib

## Testing

### Manual Test (All Phases)

```bash
# 1. Enable all features
# Edit config.yaml:
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true

# 2. Run pipeline
python3 phase2_extract.py

# 3. Check output
ls -lh output/supplemental/Breakout_and_Pursuit/
cat output/supplemental/Breakout_and_Pursuit/chapter1-supplemental.json | jq '.materials[0]'
```

### Expected Output

```
Processing: Breakout_and_Pursuit/chapter1-event.json
  Extracting supplemental material...
  Extracted supplemental material
  Enriching with online searches...
  Searching for: The Rise and Fall of the Third Reich
    ✓ Found: https://archive.org/details/...
  Enriched 1 material(s)
  Applying advanced features...
  Applied advanced features to 1 material(s)
```

## Limitations

### Current Limitations
- Single author support only
- US/EU/UK copyright law only
- ISBN lookup may fail for obscure books
- Death dates may be unknown
- Archive URLs may be temporary
- **Content verification adds 2-3 seconds per URL**
- **Citations with "Unknown" title are skipped**

### Future Enhancements
- Multiple author support
- Co-author death date handling
- Anonymous work copyright rules
- Orphan work detection
- International copyright treaties
- Enhanced license detection
- Periodical-specific metadata
- **Faster content verification methods**

## Status

✅ **Production Ready**

All three phases complete, tested, and integrated into the pipeline. Ready for use on real WWII historical documents.

## Quick Reference

### Enable Everything
```yaml
supplemental_material:
  enabled: true
  enrich_with_searches: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true
```

### Run Pipeline
```bash
python3 phase2_extract.py
```

### Check Results
```bash
find output/supplemental -name "*.json" -exec jq '.materials | length' {} \;
```

---

**Implementation Date:** March 7, 2026  
**Total Lines of Code:** 717 (249 + 243 + 225)  
**QA Score:** 9.81-10.00/10  
**Status:** ✅ Complete
