# Biographical Enrichment (Phase 3)

**Status:** Production Ready  
**Last Updated:** 2026-10-05

---

## Overview

Enriches person biographies by searching external sources (Grokipedia, Wikipedia),
capturing the person's **portrait photo**, sourcing **authoritative award citations**,
following references, and validating source URLs. Runs as Phase 3 (step 1/6) after
entity extraction — **before** the OpenSERP people pass (Grokipedia/Wikipedia are
sequenced ahead of the search engine).

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
2. **Search Wikipedia** — Wikipedia API (`prop=extracts|pageimages`, `piprop=original`), extract intro section **and the lead portrait**
3. **Capture portrait photo** — when a relevant page has a lead image, record its URL + license AND **download the photo itself**, preserving the bytes to storage (local/S3) under `person_photos/<person>/<hash>.<ext>` (see [Photo capture](#portrait-photo-capture))
4. **Grok AI extraction** — Submit source text to Grok, get structured JSON (birth/death, ranks, units, awards, education, family, source_urls)
5. **Merge** — Add new data to `biographical_profile` (simple fields only if empty, lists deduplicated)
6. **Follow references** — Up to 3 referenced entities searched via the same Grokipedia→Wikipedia strategy
7. **Validate source URLs** — Fetch each URL Grok returned, submit page content to Grok to verify relevance
8. **Source authoritative award citations** — for people in an award context whose nationality/serving-country is indicated (see [Award citations](#authoritative-award-citations))
9. **Pydantic validation** — Validate against the Person model before saving
10. **Save** — Update person JSON file in-place

---

## Portrait photo capture

When a Wikipedia biography page is found and judged military-relevant, the lead
image is captured as a **retained source record** (same principle as award pages):

- The image **URL** and **license** (via the Commons `imageinfo` API) are recorded.
- The **photo bytes** are downloaded (polite browser headers) and preserved to the
  configured storage backend under `person_photos/<person-slug>/<sha256-16>.<ext>`
  (`.jpg/.png/.gif/.webp/.svg` by content type). Idempotent; fail-safe (a
  download/preserve failure never affects text enrichment).
- `get_wikipedia_image(name)` returns `{url, license, preserved_path}`. The OpenSERP
  people pass prefers this portrait and only search-engine-image-searches as a fallback.

Code: `_store_wikipedia_image()` / `_preserve_person_photo()` in
`src/extraction/enrich_biographies.py`; preservation via
`src/enrichment/award_source_pages.preserve_page(prefix="person_photos")`.

---

## Authoritative award citations

For a person **in an award context** whose **nationality OR serving country is
indicated**, verbatim citation text + provenance are sourced from authoritative
direct sources (not search engines). Opt-in via `AWARD_CITATIONS_ENABLED=true`.

- **Registry-driven:** `config/award_sources.yaml` maps each country to its
  source(s); only `enabled` sources with an implemented adapter run.
- **Routing by awarding power, confined to indicated countries:** each award routes
  to the record system of the power that issued it (e.g. an Iron Cross → German
  sources) — **only when the person indicates that country** (citizenship or
  `nationality_served`); never broadened by award name alone.
- **Polite + preserved:** every fetch honors a per-host crawl-delay, is cached, and
  the fetched page is preserved as a source record (`award_sources/<id>/...`).
- **Translation:** non-English citations are translated to English; the verbatim
  original + language are kept (`citation_text_original`, `citation_language`).
- **Attempts + errors:** each source attempt is recorded on the award
  (`sourcing_attempts`: source/outcome/error-with-URL/date); errored attempts are
  persisted durably (`award_sourcing_errors/...`, `status=needs_retry`) for later
  retry / UI intervention.
- **WWII-only + recipient match:** citations are accepted only when the recipient
  matches and the citation is WWII-era (non-WWII same-name recipients are excluded).

Enabled live sources today: US (Hall of Valor), UK (London Gazette, Victoria Cross
Online), France (Ordre de la Libération), Canada (DHH), Italy (Quirinale), USSR
(Podvig Naroda). Many other countries are registered as name-rolls / offline
placeholders (`enabled: false`) with "where to search" archival pointers. See
[Award Sourcing](../../dataquality/AWARD_SOURCING.md).

Code: `_source_award_citations()` (enrich_biographies) → `award_registry` selector →
`enrich_person_awards()` (award_sources). Schema version 2.5.

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

**Simple fields** (only added if empty): `birth_date`, `birth_place`, `death_date`, `death_place`, `nationality`, `nationality_served`, `role_type`, `primary_group_id`, `biographical_details`

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
