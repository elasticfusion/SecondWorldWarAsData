# Supplemental Material Schema Enhancement

**Date:** 2026-03-08  
**Status:** Implemented

## Overview

Added missing fields to supplemental material schema to fully comply with offline material and archive requirements.

## Changes Made

### 1. Schema Updates (`src/json_schemas.py`)

**Added to `SUPPLEMENTAL_SCHEMA.properties.Supplemental_Material.items.properties`:**
- `archive_reference_number` - Document reference number in archive (string, nullable)
- `archive_physical_address` - Physical address of archive (string, nullable)
- `url_validation_status` - Whether URL was validated (string, nullable)
- `url_validation_date` - When URL was last validated (string, nullable)

**Added to `citation.properties`:**
- `author_death_date` - Author's death date for copyright determination (string, nullable)

### 2. Extraction Prompt Updates (`src/extraction/supplemental.py`)

**Updated `SYSTEM_PROMPT`:**
- Added instruction to extract `author_death_date` for copyright determination
- Added instruction to extract `archive_reference_number` for archive materials
- Added instruction to extract `archive_physical_address` for archive materials

**Updated example in `create_supplemental_prompt()`:**
- Added new fields to example JSON structure
- Added guidance on when to populate archive fields

## Field Specifications

### Archive Fields

**`archive_reference_number`** (string, nullable)
- Document reference number or catalog ID in archive
- Example: "NARA RG 407, Entry 427, Box 19148"
- Only populated when `availability` is "archive"

**`archive_physical_address`** (string, nullable)
- Physical address of archive location
- Example: "National Archives, College Park, MD 20740"
- Only populated when `availability` is "archive"

### Copyright Fields

**`author_death_date`** (string, nullable)
- Author's death date for copyright determination
- Format: YYYY or YYYY-MM-DD
- Example: "1969" or "1969-03-28"
- Used to calculate copyright expiration (life + 70 years in most jurisdictions)

### Validation Fields

**`url_validation_status`** (string, nullable)
- Status of URL validation
- Suggested values: "validated", "failed", "not_checked", "broken"
- Reserved for future URL validation implementation

**`url_validation_date`** (string, nullable)
- Date when URL was last validated
- Format: YYYY-MM-DD
- Reserved for future URL validation implementation

## Output Format

### Example: Archive Material

```json
{
  "MaterialID": "01KK6J70TYPGHXYGVQ7GZMNZVV",
  "reference_type": "endnote",
  "verbatim_reference": "War Department Records, RG 407, Entry 427, Box 19148",
  "citation": {
    "author": [],
    "title": "VII Corps After Action Report, June 1944",
    "document_type": "Primary source",
    "author_death_date": null
  },
  "availability": "archive",
  "resource_urls": ["https://catalog.archives.gov/id/123456"],
  "archive_reference_number": "NARA RG 407, Entry 427, Box 19148",
  "archive_physical_address": "National Archives, College Park, MD 20740",
  "url_validation_status": null,
  "url_validation_date": null,
  "license": "public_domain",
  "license_notes": "US Government document"
}
```

### Example: Book with Author Death Date

```json
{
  "MaterialID": "01KK6J70TYPGHXYGVQ7GZMNZVV",
  "reference_type": "endnote",
  "verbatim_reference": "Shirer, William L. The Rise and Fall of the Third Reich...",
  "citation": {
    "author": ["Shirer, William L."],
    "title": "The Rise and Fall of the Third Reich",
    "publisher": "Simon & Schuster",
    "publication_date": "1960",
    "first_edition_date": "1960",
    "isbn": "978-0671728694",
    "author_death_date": "1993"
  },
  "availability": "offline",
  "resource_urls": [],
  "archive_reference_number": null,
  "archive_physical_address": null,
  "license": "copyright",
  "license_notes": "Copyright expires 2063 (author death + 70 years)"
}
```

## Requirements Compliance

### ✅ Offline Material in Archives
- ✅ Reference number: `archive_reference_number`
- ✅ Physical address: `archive_physical_address`
- ✅ URL to material: `resource_urls` (array)
- ⏳ URL validation: `url_validation_status`, `url_validation_date` (reserved for future)

### ✅ Offline Books
- ✅ ISBN: `citation.isbn` (preferably first edition)
- ✅ ISBN edition: `citation.isbn_edition` (if not first edition)
- ✅ Publication location: `citation.publication_location`
- ✅ Publication date: `citation.publication_date`
- ✅ Author death date: `citation.author_death_date`
- ✅ Copyright determination: `license`, `license_notes`

### ✅ Periodicals/Journals
- ✅ Publication date: `citation.publication_date`
- ✅ Periodical name: `citation.periodical_name`
- ✅ Authors: `citation.author` (array)

## Storage Location

All supplemental material is stored in book-specific directories:

```
output/{BookName}/{chapter}-endnotes.json
output/{BookName}/{chapter}-footnotes.json
```

**Examples:**
- `output/BreakoutAndPursuit/chapter1a-endnotes.json`
- `output/Cross-Channel-Attack/chapter0-footnotes.json`

## Future Enhancements

### URL Validation (Not Yet Implemented)
The schema includes fields for URL validation, but the validation logic is not yet implemented:

1. **Validation Process:**
   - Check if URL returns HTTP 200
   - Verify content matches expected material
   - Update `url_validation_status` and `url_validation_date`

2. **Suggested Implementation:**
   ```python
   def validate_supplemental_urls(material: Dict[str, Any]) -> None:
       """Validate URLs in supplemental material."""
       for url in material.get("resource_urls", []):
           status = check_url(url)  # Returns "validated" or "broken"
           material["url_validation_status"] = status
           material["url_validation_date"] = datetime.now().strftime("%Y-%m-%d")
   ```

3. **Integration Point:**
   - Add to Phase 3 enrichment
   - Or create separate validation script
   - Run periodically to check for broken links

## Backward Compatibility

- All new fields are **nullable** (optional)
- Existing supplemental files without these fields remain valid
- Schema validation passes for both old and new formats
- No migration required for existing data

## Testing

```bash
# Verify schema changes
python3 -c "
from src.json_schemas import SUPPLEMENTAL_SCHEMA
material_props = SUPPLEMENTAL_SCHEMA['properties']['Supplemental_Material']['items']['properties']
citation_props = material_props['citation']['properties']

assert 'archive_reference_number' in material_props
assert 'archive_physical_address' in material_props
assert 'url_validation_status' in material_props
assert 'url_validation_date' in material_props
assert 'author_death_date' in citation_props

print('✅ All new fields present in schema')
"

# Syntax check
python3 -m py_compile src/json_schemas.py src/extraction/supplemental.py
```

## Related Files

- `src/json_schemas.py` - Schema definition
- `src/extraction/supplemental.py` - Extraction logic and prompts
- `output/{BookName}/*-endnotes.json` - Output files
- `output/{BookName}/*-footnotes.json` - Output files
