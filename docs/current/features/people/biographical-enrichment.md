# Biographical Enrichment (Phase 3)

**Status:** Production Ready  
**Last Updated:** 2026-03-15

---

## Overview

Enriches person biographies by searching external sources (Grokipedia, Wikipedia), following references, and validating source URLs. Runs as Phase 3 after entity extraction.

```bash
# Recommended: use retry wrapper
python3 phase3_retry.py

# Direct with options
python3 phase3_enrich_data.py --max-items 10 --log-level DEBUG
python3 phase3_enrich_data.py --no-references    # Skip reference following (faster)
python3 phase3_enrich_data.py --people-only       # Only enrich people
```

---

## Pipeline

For each person in `output/people/`:

1. **Search Grokipedia** — HTTP GET `https://grokipedia.com/search?q={name}`, extract page text
2. **Search Wikipedia** — Wikipedia API, extract intro section
3. **Grok AI extraction** — Submit source text to Grok, get structured JSON (birth/death, ranks, units, awards, education, family, source_urls)
4. **Merge** — Add new data to `biographical_profile` (simple fields only if empty, lists deduplicated)
5. **Follow references** — Up to 3 referenced entities (units, organizations) searched via same Grokipedia→Wikipedia strategy
6. **Validate source URLs** — Fetch each URL Grok returned, submit page content to Grok to verify relevance to the person
7. **Pydantic validation** — Validate against Person model before saving
8. **Save** — Update person JSON file in-place

---

## URL Validation

When Grok returns `source_urls` in its extraction response:

1. **Fetch** — HTTP GET each URL (15s timeout)
2. **Verify** — Submit first 3000 chars of page content to Grok: "Is this page about {person_name}? Does it contain relevant biographical/military data?"
3. **Store** — Validated URLs added to `biography_sources` with confidence 0.9
4. **Discard** — Broken URLs (non-200) and irrelevant pages are dropped

Up to 5 URLs validated per person.

---

## Merging Logic

**Simple fields** (only added if empty): `birth_date`, `birth_place`, `death_date`, `death_place`, `nationality`, `role_type`, `biographical_details`

**List fields** (merged, deduplicated): `ranks`, `units_served`, `education`, `military_awards`, `aliases`

**Family**: Adds spouse if missing, merges children without duplicates.

**Source tracking**: Each source adds an entry to `biography_sources`:
```json
{
  "source": "Wikipedia",
  "page": null,
  "confidence": 0.8,
  "fields_sourced": ["birth_date", "ranks"]
}
```

---

## Error Handling

- **HTTP failures** — Returns None, continues with next source
- **403 Forbidden** — Logged at warning level, no retry, continues
- **Grok extraction failures** — 2 retries (first uses cache, retry bypasses cache)
- **URL validation failures** — Broken/irrelevant URLs silently discarded
- **File errors** — Logged at error level, continues with next person
- **Pydantic validation failure** — Logged, save skipped

---

## Performance

Per person: ~2-7 API calls (1-2 HTTP searches, 1-2 Grok extractions, 0-3 reference searches, 0-5 URL validations). All Grok responses cached via diskcache. ~5-10 seconds per person.

---

## Code Reference

**Entry point:** `phase3_enrich_data.py` → `enrich_all_people()`  
**Core module:** `src/extraction/enrich_biographies.py`  
**Retry wrapper:** `phase3_retry.py`

Key functions:
- `search_grokipedia()` / `search_wikipedia()` — Source search
- `extract_biographical_data()` — Grok AI structured extraction
- `search_references()` — Follow referenced entities
- `validate_source_urls()` — Fetch + Grok relevance check
- `enrich_person_biography()` — Orchestrates full enrichment for one person

---

## Related

- [People Extraction](README.md)
- [Deduplication](deduplication.md)
- [Workflow Diagrams](../../core/WORKFLOW_DIAGRAMS.md) — Phase 3 diagram
- [Error Handling](../../core/error_handling.md)
