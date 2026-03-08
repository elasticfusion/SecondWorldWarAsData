# Supplemental Material Extraction - Phase 1 Implementation

## Overview

Phase 1 implements core extraction and citation parsing for supplemental materials (footnotes, endnotes, bibliography) from WWII historical documents.

## Implementation Status

✅ **Phase 1: Core Extraction** (Implemented)
- ULID generation for MaterialID
- Event/Sub-event linkage
- Citation parsing into structured format
- Resource availability classification
- Basic license determination

⏳ **Phase 2: Search Integration** (Not Yet Implemented)
- Gutenberg.org search
- Archive.org API integration
- OpenSERP integration
- Multi-pass search strategy

⏳ **Phase 3: Advanced Features** (Not Yet Implemented)
- ISBN extraction
- Copyright determination
- Archive URL verification

## Configuration

Add to `config.yaml`:

```yaml
supplemental_material:
  enabled: false                   # Set to true to enable
  extract_citations: true          # Parse citations into structured format
  enrich_with_searches: false      # Phase 2 (not yet implemented)
  search_gutenberg: false          # Phase 2 (not yet implemented)
  search_archive_org: false        # Phase 2 (not yet implemented)
  use_openserp: false              # Phase 2 (not yet implemented)
  verify_archive_urls: false       # Phase 3 (not yet implemented)
  max_materials_per_chapter: 100   # Limit materials per chapter
```

## Usage

### Via Phase 2 Pipeline

Enable in config and run:

```bash
python3 phase2_extract.py
```

Supplemental material will be extracted after casualties for each event file.

### Standalone Test

```bash
python3 tests/test_supplemental.py
```

## Output Structure

Files are created in `output/supplemental/` with naming pattern:
- `{Book}/chapter{N}-supplemental.json`

### JSON Structure

```json
[
  {
    "Event_Name": "Event title",
    "EventID": "01H8XYZABC123DEF456GHJ789",
    "Sub-event_Name": "Sub-event description",
    "Sub-eventID": "01H8XYZ1MN456PQR789STU012",
    "Supplemental_Material": [
      {
        "MaterialID": "01H8XZ0JSAB123CD456EF789GH",
        "EventID": "01H8XYZABC123DEF456GHJ789",
        "Sub-eventID": "01H8XYZ1MN456PQR789STU012",
        "reference_type": "endnote",
        "reference_number": "4",
        "verbatim_reference": "Shirer, William L. The Rise and Fall...",
        "citation": {
          "author": ["Shirer, William L."],
          "title": "The Rise and Fall of the Third Reich",
          "publisher": "Simon & Schuster",
          "publication_date": "1960",
          "first_edition_date": "1960",
          "publication_location": "New York",
          "publication_country": "USA",
          "isbn": null,
          "pages": "597-598"
        },
        "availability": "offline",
        "resource_urls": [],
        "license": "copyright",
        "license_notes": "Copyright - Simon & Schuster"
      }
    ]
  }
]
```

## Features

### ULID Generation
- Each material gets unique MaterialID
- Links to parent EventID and Sub-eventID
- Enables cross-referencing across the dataset

### Citation Parsing
Uses Grok API to parse unstructured citations into:
- Author(s) as array
- Title, publisher, publication details
- Location and country (ISO 3166-1 alpha-3)
- ISBN (for post-1966 books)
- Pages, volume, edition
- Periodical name (for journals)
- Document type classification

### Availability Classification
- **online**: Resource available via URL
- **offline**: General offline resource
- **archive**: Held in physical archive
- **unknown**: Cannot determine

### License Determination
- **public_domain**: Government/educational institutions
- **copyright**: Commercial publishers
- **unknown**: Cannot determine

## Error Handling

Follows project error handling standards from `contextmanagement/Specs/error_handling.md`:

### Retry Logic with Exponential Backoff
- 3 attempts per sub-event
- First attempt uses cache
- Subsequent attempts bypass cache
- Continues processing other sub-events on failure

### Validation Error Recovery
- Validation errors logged with details
- Invalid responses skipped (not retried)
- Processing continues with next sub-event

### File I/O Error Handling
- JSON decode errors caught and logged
- Missing event files handled gracefully
- Output directory creation errors caught
- File write errors logged with context

### Graceful Degradation
- One sub-event failure doesn't stop extraction
- Partial results better than no results
- Returns None if no materials extracted

### Logging Levels
- **ERROR**: Validation failures, file I/O errors, all retries exhausted
- **WARNING**: Retry attempts
- **INFO**: Progress updates, successful extractions
- **DEBUG**: Sub-event processing, invalid data details

## Data Quality

### Validation
- **JSON Schema Validation**: All output validated against `SUPPLEMENTAL_SCHEMA` before writing
- Validation errors logged with details
- Invalid responses skipped (not written to file)
- All MaterialIDs are valid ULIDs
- EventID and Sub-eventID match parent event
- Reference type is one of: endnote, footnote, bibliography
- Country codes use ISO 3166-1 alpha-3 standard
- Citation must have at least a title
- Availability must be: online, offline, archive, or unknown

### Caching
- API responses cached in `cache/api/supplemental/`
- Reduces API costs for repeated extractions
- Cache key based on prompt content

## Known Limitations

### Phase 1 Limitations
- No URL validation (Phase 2)
- No search for online versions (Phase 2)
- No copyright determination (Phase 3)
- No archive URL verification (Phase 3)
- Citation parsing depends on LLM accuracy

### Handling Edge Cases
- Pre-1966 books: ISBN will be null
- Unnumbered bibliography: reference_number is null
- Symbol references: Stored as-is (e.g., "*", "†")
- Unknown dates: Use "UNKNOWN" string
- Missing fields: Use null

## Testing

Run the test script to validate extraction:

```bash
python3 tests/test_supplemental.py
```

Expected output:
- Finds first event file
- Extracts supplemental material
- Displays sample materials with parsed citations
- Shows MaterialID, availability, license

## Next Steps

### Phase 2: Search Integration
1. Implement Gutenberg.org search via OpenSERP
2. Add Archive.org API integration
3. Implement sequential search strategy
4. Add URL validation
5. Track search metadata

### Phase 3: Advanced Features
1. ISBN extraction for books
2. Copyright determination using author death dates
3. Archive URL verification
4. Enhanced license detection

## Related Files

- `src/extraction/supplemental.py` - Core extraction logic
- `contextmanagement/Specs/supplementalmaterial_v2.json` - JSON schema
- `contextmanagement/Specs/supplementalmaterial_v2.md` - Full specification
- `contextmanagement/Specs/copyright-book.md` - Copyright guidance
- `contextmanagement/Specs/copyright-periodical.md` - Periodical copyright
- `tests/test_supplemental.py` - Test script

## Troubleshooting

### No materials extracted
- Check if event file has Endnote_References or Footnote_References
- Verify Grok API is accessible
- Check cache for previous failed attempts

### Invalid ULIDs
- Ensure ulid-py is installed: `pip install ulid-py`
- Check that generate_ulids() is called on response

### Validation errors
- Check logs for specific validation error messages
- Review Grok API response in cache
- Ensure all required fields are present
- Verify ULID format (26 characters, base32)
- Check reference_type is one of: endnote, footnote, bibliography
- Verify availability is one of: online, offline, archive, unknown

### Citation parsing errors
- Review verbatim_reference for unusual formatting
- Check Grok API response in cache
- May need to adjust system prompt for edge cases

## Performance

- ~1-2 API calls per sub-event
- Cached responses reused on subsequent runs
- Processing time: ~5-10 seconds per event file
- Cost: ~$0.01-0.02 per event file (Grok API)
