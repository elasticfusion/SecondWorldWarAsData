# Biographical Enrichment

**Version:** 1.0.0  
**Status:** Ready for testing  
**Last Updated:** 2026-03-02

---

## Overview

Automatically enriches person biographies by searching external sources (Grokipedia, Wikipedia) after initial extraction. Follows references for deeper enrichment.

---

## Features

### 1. Multi-Source Search
Searches in priority order:
1. **Grokipedia** - First priority
2. **Wikipedia** - Second priority
3. **References** - Follows mentioned entities (units, organizations)

### 2. Reference Following
When biographical data mentions entities (units served, organizations), automatically searches those for additional context.

### 3. Smart Merging
- Only adds data if field is empty
- Deduplicates list items (ranks, awards, etc.)
- Tracks sources with lower confidence (0.8 for external)
- Preserves existing data

### 4. Source Tracking
Automatically adds to `biography_sources`:
```json
{
  "source": "Wikipedia",
  "page": null,
  "confidence": 0.8,
  "fields_sourced": ["birth_date", "ranks", "units_served"]
}
```

---

## Usage

### Command Line

```bash
# Enrich all people
python3 -m src.extraction.enrich_biographies

# Enrich first 10 people
python3 -m src.extraction.enrich_biographies --max-people 10

# Don't follow references (faster)
python3 -m src.extraction.enrich_biographies --no-references
```

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.enrich_biographies import enrich_all_people

people_dir = Path("output/people")
grok_client = GrokClient(Path("cache/grok_cache"))

# Enrich all people with reference following
enriched_count = enrich_all_people(
    people_dir,
    grok_client,
    max_people=None,  # All people
    search_references_flag=True  # Follow references
)

print(f"Enriched {enriched_count} people")
```

### Single Person

```python
from src.extraction.enrich_biographies import enrich_person_biography

person_file = Path("output/people/Dwight_D_Eisenhower_01KJ3ABC.json")

enriched = enrich_person_biography(
    person_file,
    grok_client,
    search_references_flag=True
)
```

---

## Search Strategy

### 1. Grokipedia Search
- Searches: `https://grokipedia.com/search?q={person_name}`
- User-Agent: Bot identification
- Extracts full page content
- Parses with Grok for structured data

### 2. Wikipedia Search
- Uses Wikipedia API: `https://en.wikipedia.org/w/api.php`
- Gets intro extract (summary section)
- User-Agent: Bot identification
- Parses with Grok for structured data

### 3. Reference Search
- Extracts references from biographical data
- Searches each reference (max 3)
- Same search strategy (Grokipedia → Wikipedia)
- Merges additional context

---

## Data Extraction

### Grok Prompt
Extracts structured data from source text:
- Birth/death dates and places
- Nationality
- Military ranks with dates
- Units served with periods
- Education
- Military awards
- Family (spouse, children)
- Aliases
- Biographical summary
- **References** (for follow-up searches)

### Confidence Levels
- **Source material:** 0.9-1.0 (high confidence)
- **External sources:** 0.8 (medium confidence)
- Allows prioritizing source material over external data

---

## Merging Logic

### Simple Fields
Only adds if field is empty:
- `birth_date`, `birth_place`
- `death_date`, `death_place`
- `nationality`, `role_type`
- `biographical_details`

### List Fields
Merges without duplicates:
- `ranks` - Military rank progression
- `units_served` - Service history
- `education` - Educational background
- `military_awards` - Decorations
- `aliases` - Alternative names

### Family
- Adds spouse if missing
- Merges children without duplicates

### Source Tracking
Adds entry to `biography_sources` with:
- Source name (Grokipedia/Wikipedia)
- Confidence: 0.8
- Fields sourced: List of added fields

---

## Example Output

### Before Enrichment
```json
{
  "PersonID": "01KJ3ABC...",
  "name": "Dwight D. Eisenhower",
  "biographical_profile": {
    "birth_date": null,
    "nationality": null,
    "ranks": [],
    "biography_sources": []
  }
}
```

### After Enrichment
```json
{
  "PersonID": "01KJ3ABC...",
  "name": "Dwight D. Eisenhower",
  "biographical_profile": {
    "birth_date": "1890-10-14",
    "nationality": "American",
    "ranks": [
      {"rank": "General of the Army", "date": "1944-12-20", "branch": "US Army"}
    ],
    "biography_sources": [
      {
        "source": "Wikipedia",
        "page": null,
        "confidence": 0.8,
        "fields_sourced": ["birth_date", "nationality", "ranks"]
      }
    ]
  }
}
```

---

## Performance

### API Calls
Per person:
- 1-2 HTTP requests (Grokipedia, Wikipedia)
- 1-2 Grok API calls (extraction)
- 0-3 additional searches (references)

**Total:** ~2-7 API calls per person

### Caching
- Grok extractions cached
- HTTP responses not cached (fresh data)
- Reduces cost on re-runs

### Rate Limiting
- Respects site policies
- Uses proper User-Agent
- No built-in delays (add if needed)

---

## Configuration

### Search Limits
```python
max_references: int = 3  # Max references to follow
```

### Timeouts
```python
timeout: int = 30  # HTTP request timeout (seconds)
```

### Text Limits
```python
source_text[:5000]  # First 5000 chars sent to Grok
```

---

## Error Handling

### HTTP Failures
- Returns `None` on failure
- Logs at debug level
- Continues with next source

### 403 Forbidden Errors
- Detected in both response and exception
- Logs at warning level (visible)
- No retry (won't succeed)
- Suggests checking User-Agent
- Continues with next person

### Extraction Failures
- Returns `None` on Grok error
- Logs at debug level
- Continues with next person

### Retry Logic
- **HTTP requests:** 2 retries on timeout
- **Grok extraction:** 2 retries with cache bypass
- **First attempt:** Uses cache (fast)
- **Retry:** Bypasses cache (fresh data)

### File Errors
- Logs at error level
- Continues with next person
- Returns False (not enriched)

---

## Integration

### After Person Extraction
```python
# In phase2_extract.py or people extraction
from src.extraction.enrich_biographies import enrich_person_biography

# After saving person file
person_file = people_dir / f"{filename}.json"
with open(person_file, "w") as f:
    json.dump(person_data, f, indent=2)

# Enrich from external sources
enrich_person_biography(person_file, grok_client)
```

### Batch Processing
```python
# After all extractions complete
from src.extraction.enrich_biographies import enrich_all_people

enriched = enrich_all_people(
    people_dir,
    grok_client,
    max_people=None,
    search_references_flag=True
)
```

---

## Limitations

### Grokipedia
- May not have all WWII figures
- Content quality varies
- Search may return no results

### Wikipedia
- API returns intro only (not full article)
- May not have detailed military data
- Some figures may not have pages

### References
- Limited to 3 references per person
- May not find all referenced entities
- Depends on reference name accuracy

---

## Future Enhancements

1. **More sources** - Add military archives, historical databases
2. **Better reference extraction** - NER for entity recognition
3. **Conflict resolution** - Handle contradicting data
4. **Incremental enrichment** - Only search if data is sparse
5. **Quality scoring** - Rate source reliability
6. **Rate limiting** - Respect API limits
7. **Parallel processing** - Speed up batch enrichment

---

## Quality Assurance

- ✅ Pylint: 9.23/10
- ✅ Mypy: 0 errors
- ✅ Black: Formatted

---

## Related Documentation

- **People Extraction:** `docs/current/features/people/implementation.md`
- **Source Tracking:** `docs/current/features/people/source-tracking.md`
- **Error Handling:** `docs/current/core/error_handling.md`

---

## Example Run

```bash
$ python3 -m src.extraction.enrich_biographies --max-people 5

Enriching 5 people from external sources...
============================================================
Enriching: Dwight D. Eisenhower
  Searching Grokipedia...
  Searching Wikipedia...
  Following 2 reference(s)...
  Searching reference: Supreme Headquarters Allied Expeditionary Force
  ✅ Enriched Dwight D. Eisenhower
Enriching: George S. Patton Jr.
  Searching Grokipedia...
  Searching Wikipedia...
  No new data found
============================================================
Enrichment complete: 1/5 people enriched
```
