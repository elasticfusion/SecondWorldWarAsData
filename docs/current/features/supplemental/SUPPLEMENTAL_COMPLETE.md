# Supplemental Material Extraction - Complete Implementation

> **2026-11-07 — Phase 2 performs no supplemental searches.** Phase 2 only talks to Grokipedia/Wikipedia and
> submits data to Grok. Looking up where a cited document can be obtained (NARA, archive.org, Gutenberg,
> OpenSERP, Grok "LLM search") is Phase 3 (`bibliography_resolver`), with every candidate Grok-verified. The
> `enrich_with_searches` flag no longer exists; citations are stored with an empty `resource_urls`.


> **2026-11-16 — how the request is asked.** Supplemental runs over 3-paragraph windows of each
> sub-event's own raw paragraphs (`supplemental._window_prompts`, `windows.sub_event_windows`); only a
> window carrying a note marker is asked, and a note once per sub-event. `reference_number`,
> `reference_type` (when the model gave none) and `verbatim_reference` are assigned deterministically
> afterwards by `supplemental.locate_notes`; see [ENDNOTE_HANDLING.md](ENDNOTE_HANDLING.md). In the
> sections below, "Phase 1/2/3" are this feature's own stages, not pipeline phases: the extraction
> and routing stage runs in pipeline Phase 2; the search and advanced stages run in pipeline Phase 3.

## Overview

Extracts, classifies, and routes referenced materials (footnotes, endnotes, bibliography) from WWII historical documents. Uses a split architecture that separates document references from factual content.

## Architecture: Supplemental Split

Grok classifies each endnote/footnote with `content_class` (`_route_one_material`):

- `citation` (legacy `document_reference`) — pure citation → `output/bibliography/` (deduplicated, one file per document), if `_has_document` finds a real title/author/publisher, an archive reference number, or a verbatim reference of at least 8 characters
- `prose` (legacy `factual_content`) — historical narrative → `{chapter}-notes-event.json` in the event file's directory (`_write_notes_event`)
- `hybrid` — both: the citation half goes to the bibliography and the prose half to the notes-event
- `ambiguous`, or any material that routed to neither place (bare cross-reference, empty citation and prose) → `output/bibliography/review_queue.json` for human review (`_append_to_review_queue`)

How the endnote/footnote **text** reaches the prompt (parse-time capture, HTML fallback,
"cited in n. X" resolution, semicolon splitting) is in [ENDNOTE_HANDLING.md](ENDNOTE_HANDLING.md).

### Key Files
- `src/extraction/supplemental.py` — classification, routing, extraction
- `src/extraction/bibliography.py` — deduplicated document storage with fuzzy title matching
- `src/extraction/supplemental_search.py` — URL search helpers, called from the Phase 3 `src/enrichment/bibliography_resolver.py`
- `src/extraction/supplemental_advanced.py` — ISBN/copyright, `enrich_bibliography` (Phase 3)

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
- Basic license detection (`_apply_license`, pure computation)
- ISO 3166-1 alpha-3 country codes
- JSON schema validation before file write
- ULID fixing via `_fix_invalid_ulids` for Grok's fake ULIDs
- Retry (3 attempts; the cache is used on the first only; a schema-validation failure is not retried)
- Anachronistic citation filtering (rejects citations newer than source copyright year)
- Factual content written as event-like JSON for downstream entity extraction
- Bibliography deduplication with fuzzy title matching

### ✅ Search Integration (Complete; runs in pipeline Phase 3)
**File:** `src/extraction/supplemental_search.py`, driven by `src/enrichment/bibliography_resolver.py` (the sequence below is the original design; the resolver now orders and verifies candidates itself)

**Features:**
- Gutenberg.org search (Gutendex API) and Archive.org Advanced Search API
- NARA catalog and OpenSERP candidates
- Grok "LLM search" as a last resort (cached)
- Every candidate is Grok-verified against the citation before it is kept (prevents hallucinated URLs)
- Citations with no usable title/verbatim are skipped (`not_citation`)

**Search order** (`bibliography_resolver._search_online_sources`, aggregated, not first-hit):
1. NARA (`_find_in_nara`)
2. OpenSERP (`_find_in_openserp`)
3. Archive.org (`_find_in_archive_org`)
4. Gutenberg (`_find_in_gutenberg`)
5. Grok LLM search (`_find_in_llm`), only when 1-4 found nothing

Resolver choice depends on `document_type`/`availability` (`_pick_resolver`): archive types and
`availability: archive` go to the NARA/archive resolver; books try Archive.org/Gutenberg/OpenSERP
(then `alt_title`, then the LLM); articles use the article search; untyped entries use the full order
above. Entries already held locally (`local_holdings`), and entries with `resource_urls`, are
short-circuited. All verified findings are kept in `resource_urls` and `source_metadata.findings`.

**Content Verification:**
- Uses Grok to verify URL matches citation
- Checks title and author
- Rejects incorrect URLs (e.g., SoundCloud when expecting book)
- Prevents hallucinated results

### ✅ Advanced Features (Complete; pipeline Phase 3, off in config)
**File:** `src/extraction/supplemental_advanced.py`

**Features:**
- ISBN extraction for books (post-1966)
- Author death date lookup via LLM
- Copyright determination (USA, EU, UK)
- Archive URL verification

**Copyright Rules:**
- USA: Pre-1928 (public domain), 1928-1977 (95 years), Post-1977 (Life+70)
- EU/UK: Life + 70 years
- Government works: Public domain


## Configuration

```yaml
supplemental_material:
  # Phase 1: Core extraction
  enabled: true                    # Enable supplemental material extraction (config.yaml: true)
  extract_citations: true          # (config.yaml: true; no code reads it)
  max_materials_per_chapter: 500   # (config.yaml: 500; no code reads it)
  
  # Phase 3: search (bibliography_resolver) - Phase 2 performs NO supplemental searches
  llm_search: true                 # Use LLM for search (first pass)
  search_gutenberg: true           # Search Gutenberg.org for books/periodicals
  search_archive_org: true         # Search Archive.org (second pass)
  use_openserp: true               # Use OpenSERP for web search (third pass)
  # OpenSERP URL is external_maps.openserp_url
  
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
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true
```

```bash
python3 phase2_extract.py      # extraction + routing
python3 phase3_enrich_data.py  # searches (bibliography_resolver) and advanced features
```

### Standalone Usage

```python
from pathlib import Path
from src.extraction.supplemental import extract_supplemental
from src.grok_client import GrokClient

# Phase 1: Extract + route (bibliography + factual content)
notes_file = extract_supplemental(
    event_file=Path("output/content/<book>/chapter1-event.json"),
    grok_client=GrokClient(Path("cache")),
    output_root=Path("output"),
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
1. Load event file and the chapter's parsed file
2. Fetch note texts (`fetch_endnote_texts`), then for each note-bearing window of each sub-event:
   - Build prompt with reference context
   - After the answer, `locate_notes` sets reference_number / verbatim_reference deterministically
   - Grok extracts structured citations with `content_class`
   - `_fix_invalid_ulids` fixes Grok's fake ULIDs
   - `generate_ulids` replaces GENERATE_NEW_ULID placeholders
   - `sanitize_supplemental_data` applies field defaults
   - `validate_supplemental_json` validates against schema
3. Route by `content_class` (`_route_by_content_class`):
   - `citation` / `hybrid` → `bibliography.store_bibliography_entry()` (merge or create)
   - `prose` / `hybrid` → `_write_notes_event()` as event-like JSON
   - `ambiguous` or unrouted → `_append_to_review_queue()`
4. Filter anachronistic citations (newer than source copyright year)

### Phase 2: Search Integration (pipeline Phase 3, `bibliography_resolver`)
1. Load the bibliography entries
2. For each material without `resource_urls`: search in the order given under "Search order" and Grok-verify each candidate
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

### API Calls
- Pipeline Phase 2: 1 Grok request per note-bearing window of a sub-event (`cache_type` `supplemental`, temperature 0.1; one `ask_all` batch round).
- Pipeline Phase 3: per bibliography entry, Grok verification of each search candidate, the `alt_title` expansion and (last) the LLM search; ISBN and author-death-date lookups only when `extract_isbn` / `determine_copyright` are on (both false in `config.yaml`).

## Error Handling

### Graceful Degradation
- Phase 1 failure → No supplemental file created
- Phase 2 failure → Original data preserved
- Phase 3 failure → Original data preserved
- Individual material failures → Continue with next

### Retry Logic
- Extraction: 3 attempts (see Validation).
- Search and advanced stages: no retries.

### Timeouts
- Grok requests: `GrokClient` defaults (see `src/grok_client.py`).
- Archive.org metadata and URL checks: per-call timeouts in `supplemental_search.py` and `supplemental_advanced.verify_archive_url` (10 s default).

## Documentation

### Specification
- `docs/archive/contextmanagement/Specs/supplementalmaterial_v2.md` - Full specification (historical; current schema: `src/schemas/`)
- `docs/archive/contextmanagement/Specs/supplementalmaterial_v2.json` - JSON schema (historical; current schema: `src/schemas/`)

### Implementation Docs
- `docs/archive/2026-03-22/SUPPLEMENTAL_PHASE1.md` - Phase 1 details (archived)
- `docs/archive/2026-03-22/SUPPLEMENTAL_PHASE2.md` - Phase 2 details (archived)
- `docs/archive/2026-10-11/features/supplemental/SUPPLEMENTAL_ERROR_HANDLING.md` - Error handling (archived)
- [SUPPLEMENTAL_VALIDATION.md](SUPPLEMENTAL_VALIDATION.md) - Validation details

### This Document
- Complete overview of all phases
- Configuration guide
- Usage examples
- Performance metrics

## Testing

### Manual Test (All Phases)

```bash
# 1. Enable all features
# Edit config.yaml:
supplemental_material:
  enabled: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true

# 2. Run pipeline
python3 phase2_extract.py && python3 phase3_enrich_data.py

# 3. Check output
ls -lh output/bibliography/ | head
jq '.mentions[0]' output/bibliography/<one file>.json
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

Implemented. Extraction and routing run in pipeline Phase 2; the search and advanced stages run in pipeline Phase 3 (`bibliography_resolver`, `enrich_bibliography`).

## Quick Reference

### Enable Everything
```yaml
supplemental_material:
  enabled: true
  llm_search: true
  search_archive_org: true
  extract_isbn: true
  determine_copyright: true
  verify_archive_urls: true
```

### Run Pipeline
```bash
python3 phase2_extract.py
python3 phase3_enrich_data.py
```

### Check Results
```bash
ls output/bibliography/*.json | wc -l
jq 'length' output/bibliography/review_queue.json
```

