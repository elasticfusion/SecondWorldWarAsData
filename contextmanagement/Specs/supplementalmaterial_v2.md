# Supplemental Material Specification v2

## Overview

This specification defines the structure and requirements for extracting, enriching, and tracking referenced materials (footnotes, endnotes, bibliography) from historical documents.

## Core Requirements

### Unique Identification
- Every unique material **SHALL** have a ULID (`MaterialID`)
- Every unique material **SHALL** reference the parent event (`EventID`) and sub-event (`Sub-eventID`)

### Reference Classification
- Every unique material **SHALL** note the type: `endnote`, `footnote`, or `bibliography`
- Every unique material **SHALL** include `reference_number`:
  - Bibliography entries frequently won't be numbered (use `null`)
  - Endnotes/footnotes may contain symbols (e.g., `*`, `†`, `‡`) - include the symbol as-is
  - Standard numeric references use string or integer

### Citation Information
- All unique material **SHALL** include verbatim reference text
- All unique material **SHALL** include parsed citation components:
  - Author(s)
  - Title
  - Publisher (if applicable)
  - Publication date (or `"UNKNOWN"`)
  - For books with long publication history, use first edition date
  - Publication location and country
  - ISBN (for post-1966 books, preferably first edition)
  - Pages, volume, edition (if applicable)

### Resource Availability
- Every unique material **SHALL** determine availability: `online`, `offline`, `archive`, or `unknown`
- Occasionally material is not publicly available (e.g., personal papers held by family) - this **SHALL** be noted in `license_notes` or `archive_info.access_restrictions`

## Online Material Requirements

### URL Extraction
- Every online material **SHALL** have URLs extracted to `resource_urls` array
- A separate utility will download the material

### License Determination
- Every online material **SHALL** have license determined:
  - Government and educational institutions **SHALL** by default be considered `public_domain`
  - If license cannot be determined, use `unknown`
  - Capture URL to the material only (not the entire site)

### Search Strategy

#### Books and Periodicals - Gutenberg.org
- **SHALL** search gutenberg.org for book and periodical material only
- Any material found on gutenberg.org **SHALL** be assumed `public_domain`
- Use OpenSERP as first pass for this search
- Store result in `search_metadata.gutenberg_url`

#### Periodicals and Journals - Sequential Search
**SHALL** search in sequential order. If valid URL found, discontinue additional searches:

1. **FIRST PASS: LLM Search**
   - Use Grok API to search for material
   - Set `search_metadata.llm_search_checked = true`

2. **SECOND PASS: Archive.org**
   - Search using Archive.org Advanced Search API
   - Base URL: `https://archive.org/advancedsearch.php`
   - Set `search_metadata.archive_org_checked = true`
   - Store result in `search_metadata.archive_org_url`

3. **THIRD PASS: OpenSERP**
   - Use OpenSERP for web search
   - Set `search_metadata.openserp_checked = true`

**Capture for periodicals/journals:**
- Publication date
- Periodical name
- Author(s)
- Publication location
- Publication country
- URL to the periodical or journal only

## Offline Material in Archives

### Government and Educational Archives
- Note reference number in `archive_info.reference_number`
- Note physical address in `archive_info.physical_address`
- **SHALL** note URL to the material in `archive_info.archive_url`
- **SHALL** validate with test that material is actually listed at the URL
  - Set `archive_info.verified = true` if validated
  - Record `archive_info.verification_date`

## Offline Material - General

### Books
1. **ISBN Extraction**
   - Attempt to find ISBN (preferably first edition)
   - Books published before 1966 will not have ISBN
   - Books published after 1966 might have ISBN for subsequent editions
   - Note edition number in `citation.isbn_edition` if not first edition

2. **Publication Information**
   - Publication location (`citation.publication_location`)
   - Publication date (`citation.publication_date`)
   - First edition date if applicable (`citation.first_edition_date`)

3. **Copyright Determination**
   - Attempt to find author's death date (`copyright_status.author_death_date`)
     - Death date may be `"UNKNOWN"`
   - Review copyright laws (see table below)
   - Determine copyright status (`copyright_status.status`)
   - Document determination basis (`copyright_status.determination_basis`)
   - Note jurisdiction (`copyright_status.jurisdiction`)

### Periodicals and Journals
- Publication date
- Periodical name (`citation.periodical_name`)
- Author(s) (`citation.author`)
- Publication location
- Publication country

## Copyright Laws Reference

### United States
| Publication Date | Copyright Status | Expiration |
|-----------------|------------------|------------|
| Before 1928 | Public Domain | Expired |
| 1928-1977 | 95 years from publication | Varies |
| After 1977 | Life + 70 years | Author death + 70 |
| Government works | Public Domain | N/A |

### European Union
| Publication Date | Copyright Status | Expiration |
|-----------------|------------------|------------|
| Any | Life + 70 years | Author death + 70 |
| Government works | Varies by country | Check jurisdiction |

### United Kingdom
| Publication Date | Copyright Status | Expiration |
|-----------------|------------------|------------|
| Any | Life + 70 years | Author death + 70 |
| Crown copyright | 125 years from creation | Or 50 years from publication |

## Data Structure

See `supplementalmaterial_v2.json` for complete JSON schema.

### Key Fields

```json
{
  "MaterialID": "ULID",
  "EventID": "ULID",
  "Sub-eventID": "ULID",
  "reference_type": "endnote|footnote|bibliography",
  "reference_number": "string|integer|null",
  "verbatim_reference": "string",
  "citation": { /* parsed citation */ },
  "availability": "online|offline|archive|unknown",
  "resource_urls": ["url1", "url2"],
  "license": "public_domain|cc0|cc-by|...|copyright|unknown",
  "search_metadata": { /* search tracking */ },
  "archive_info": { /* archive details */ },
  "copyright_status": { /* copyright determination */ }
}
```

## Implementation Notes

### Phase 1: Core Extraction
- ULID generation
- Event/sub-event linkage
- Basic citation parsing
- Resource type classification

### Phase 2: Search Integration
- Gutenberg.org search (OpenSERP)
- Archive.org API integration
- Multi-pass search strategy
- URL validation

### Phase 3: Advanced Features
- ISBN extraction
- License detection
- Copyright determination
- Archive verification

### Configuration

```yaml
supplemental_material:
  enabled: false  # Opt-in feature
  extract_citations: true
  enrich_with_searches: false  # Expensive, separate step
  search_gutenberg: true
  search_archive_org: true
  use_openserp: true
  verify_archive_urls: false  # Manual verification recommended
  max_materials_per_chapter: 100
```

## Processing Workflow

1. **Extract** - Parse footnotes/endnotes/bibliography from source
2. **Parse** - Use Grok to parse citation into structured fields
3. **Classify** - Determine availability (online/offline/archive)
4. **Search** - If online, execute search strategy
5. **Enrich** - Add license, copyright, archive information
6. **Verify** - Validate URLs and archive references
7. **Cache** - Store results with search metadata

## Quality Assurance

- All URLs **SHALL** be validated before storage
- Archive URLs **SHALL** be verified to contain material listing
- Copyright determinations **SHALL** document basis
- Search metadata **SHALL** track all attempts
- Manual review workflow recommended for complex citations

## Related Specifications

- `dates.md` - Date extraction and formatting
- `places.md` - Geographic entity extraction
- `ISO_COUNTRY_CODES.md` - Country code standards
