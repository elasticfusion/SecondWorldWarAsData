# Supplemental Material Extraction - Complete Implementation

## Overview

Extracts, classifies, and routes referenced materials (footnotes, endnotes, bibliography) from WWII historical documents. Uses a split architecture that separates document references from factual content.

## Architecture: Supplemental Split

Grok classifies each endnote/footnote into one of three categories:

- `document_reference` — pure citation → `output/bibliography/` (deduplicated, one file per document)
- `factual_content` — historical narrative with extractable entities → `output/{Book}/{chapter}-notes-event.json`
- `ambiguous` — unclear classification → `output/bibliography/review_queue.json` for human review

Mixed entries (factual statement + citation) are split into separate entries.

### Key Files
- `src/extraction/supplemental.py` (922 lines) — classification, routing, extraction
- `src/extraction/bibliography.py` (152 lines) — deduplicated document storage with fuzzy title matching
- `src/extraction/supplemental_search.py` (243 lines) — Phase 2 URL search
- `src/extraction/supplemental_advanced.py` (225 lines) — Phase 3 ISBN/copyright

## Implementation Status

### ✅ Phase 1: Core Extraction + Split Architecture (Complete)
**File:** `src/extraction/supplemental.py`

**Features:**
- ULID generation for MaterialID, EventID, Sub-eventID linkage
- Structured citation parsing (author, title, publisher, dates, ISBN, etc.)
- `alt_title` — expanded/unabbreviated form of titles (e.g., "CI 47" → "Combat Interview 47")
- `content_class` routing — Grok classifies each note
- Reference type classification (endnote/footnote/bibliography)
- Availability determination (online/offline/archive/unknown)
- Basic license detection (public_domain/copyright/unknown)
- ISO 3166-1 alpha-3 country codes
- JSON schema validation before file write
- ULID fixing via `_fix_invalid_ulids` for Grok's fake ULIDs
- Retry logic with exponential backoff (3 attempts)
- Anachronistic citation filtering (rejects citations newer than source copyright year)
- Factual content written as event-like JSON for downstream entity extraction
- Bibliography deduplication with fuzzy title matching

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
from src.grok_client import GrokClient

# Phase 1: Extract + route (bibliography + factual content)
supplemental_file = extract_supplemental(
    event_file=Path("output/BreakoutAndPursuit/chapter1-event.json"),
    grok_client=GrokClient(Path("cache")),
    output_dir=Path("output"),
)
# Creates: output/bibliography/*.json, output/BreakoutAndPursuit/chapter1-notes-event.json
```

## Output Structure

### Bibliography (`output/bibliography/`)

One JSON file per unique document, deduplicated across chapters and books:

```json
{
  "BibliographyID": "01HQXYZ123...",
  "title": "First U.S. Army, Report of Operations",
  "alt_title": null,
  "citation": {
    "author": ["First U.S. Army"],
    "title": "First U.S. Army, Report of Operations",
    "publisher": "...",
    "publication_date": "...",
    "document_type": "Primary source"
  },
  "availability": "archive",
  "resource_urls": [],
  "archive_physical_address": "NARA, College Park, MD, USA",
  "license": "public_domain",
  "mentions": [
    {
      "MentionID": "01XXXX...",
      "EventID": "...",
      "Sub-eventID": "...",
      "book": "Breakout and Pursuit",
      "chapter": "chapter3a",
      "reference_type": "endnote",
      "reference_number": "21",
      "verbatim_reference": "First U.S. Army, Report of Operations, I, 80",
      "pages": "80",
      "volume": "I"
    }
  ]
}
```

### Factual Content (`output/{Book}/{chapter}-notes-event.json`)

Structurally identical to normal event files so existing entity extractors work unchanged:

```json
{
  "Chapter": "chapter7b-notes",
  "Event": {
    "EventID": "01XXXX...",
    "Sub-events": [
      {
        "Sub-eventID": "01YYYY...",
        "Sub-event_summary": "DSC awards for actions on 10 July",
        "Sub-event_fulltext": { "paragraph_1": "Capt. Harry L. Gentry..." },
        "source_reference": {
          "reference_type": "endnote",
          "reference_number": "18",
          "source_EventID": "01KKWTB0C1...",
          "source_Sub-eventID": "01KKWTB0C2..."
        }
      }
    ]
  }
}
```

### Review Queue (`output/bibliography/review_queue.json`)

Ambiguous items queued for human review:

```json
[
  {
    "book": "BreakoutAndPursuit",
    "chapter": "chapter7b",
    "reference_number": "18",
    "verbatim_reference": "...",
    "EventID": "...",
    "Sub-eventID": "..."
  }
]
```

## Processing Flow

### Phase 1: Core Extraction + Split Routing
1. Load event file
2. For each sub-event with endnotes/footnotes:
   - Build prompt with reference context
   - Grok extracts structured citations with `content_class`
   - `_fix_invalid_ulids` fixes Grok's fake ULIDs
   - `generate_ulids` replaces GENERATE_NEW_ULID placeholders
   - `sanitize_supplemental_data` applies field defaults
   - `validate_supplemental_json` validates against schema
3. Route by `content_class`:
   - `document_reference` → `bibliography.store_bibliography_entry()` (merge or create)
   - `factual_content` → `_write_notes_event()` as event-like JSON
   - `ambiguous` → `_append_to_review_queue()`
4. Filter anachronistic citations (newer than source copyright year)

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

**Implementation Date:** March 7, 2026 (Phase 1-3), March 19, 2026 (Split Architecture)  
**Total Lines of Code:** 1,542 (922 + 152 + 243 + 225)  
**Status:** ✅ Complete
