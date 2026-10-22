# Prompt Reference

A catalog of every LLM prompt in the pipeline: a link to the prompt file, **the code
context it is invoked in** (file + function), and the runtime context (variables
interpolated into the template, and what the call produces).

- **Prompt files** live in [`prompts/`](../../prompts/) as YAML with `system_prompt`,
  `prompt_template`, and (for structured extractors) a `schema` block.
- **How they're loaded:** [`src/utils/prompt_loader.py`](../../src/utils/prompt_loader.py) —
  `render_prompt(name, **vars)` fills the template (single-pass substitution), and
  `get_system_prompt(name)` returns the system message. The `{schema}` placeholder is
  injected from the YAML's `schema` block, not from caller vars.
- **Context variables** below are the `{placeholders}` the invoking code fills in
  (excluding `{schema}`). They define what real data the prompt sees at call time.
- **Cache lane** = the `cache_type` the request is stored/batched under (groups a prompt's
  calls in the Grok Batch API and the on-disk cache).

> Generated 2026-10-22. Prompt count: **31**. Links are repo-relative.

---

## Phase 2 — Core extraction (events + dependent entities)

These run inside `phase2_extract.py` → `src/extraction/*`. Events are extracted first
(one batch), then per-event dependent entities (a second "optional" batch).

| Prompt | Invoked in (context) | Context vars | Output |
|--------|----------------------|--------------|--------|
| [`events`](../../prompts/events.yaml) | [`src/extraction/events.py`](../../src/extraction/events.py) `extract_events` / `_build_*` — the top-level event+sub-event extraction per chapter (live AND batch-collect path; this is the ONLY events prompt the pipeline uses) | `book`, `author`, `chapter`, `paragraphs`, `images_text`, `maps_text` | JSON event tree (schema) |
| [`dates`](../../prompts/dates.yaml) | [`src/extraction/dates.py`](../../src/extraction/dates.py) (`get_system_prompt("dates")`) — per sub-event date extraction | `event_id`, `event_name`, `sub_event_id`, `sub_event_summary`, `text` | JSON dates (schema) |
| [`places`](../../prompts/places.yaml) | [`src/extraction/places.py`](../../src/extraction/places.py) (`get_system_prompt("places")`) — per sub-event place extraction | `event_id`, `event_name`, `sub_event_id`, `sub_event_summary`, `text` | JSON places (schema) |
| [`people`](../../prompts/people.yaml) | [`src/extraction/people.py`](../../src/extraction/people.py) (`get_system_prompt("people")`) — per sub-event person extraction | `book`, `author`, `series`, `event_id`, `event_name`, `sub_event_id`, `sub_event_summary`, `text` | JSON people (schema) |
| [`people_groups`](../../prompts/people_groups.yaml) | [`src/extraction/people_groups.py`](../../src/extraction/people_groups.py) `render_prompt("people_groups")` — military unit / group extraction | `text` | JSON groups (schema) |
| [`equipment`](../../prompts/equipment.yaml) | [`src/extraction/equipment.py`](../../src/extraction/equipment.py) — per-event equipment extraction (optional batch) | `event_data` | JSON equipment (schema) |
| [`weather_batch`](../../prompts/weather_batch.yaml) | [`src/extraction/weather_central.py`](../../src/extraction/weather_central.py:820) (`get_system_prompt("weather_batch")`) — per-event weather extraction (optional batch) | `event_id`, `event_name`, `sub_event_blocks` | JSON weather (schema) |
| [`casualties`](../../prompts/casualties.yaml) | [`src/extraction/casualties.py`](../../src/extraction/casualties.py) — per sub-event casualty extraction (optional batch) | `entity_context`, `sub_event_block` | JSON casualties (schema) |
| [`logistics`](../../prompts/logistics.yaml) | [`src/extraction/casualties.py`](../../src/extraction/casualties.py) / [`equipment.py`](../../src/extraction/equipment.py) — per sub-event logistics extraction (optional batch) | `sub_event_block` | JSON logistics (schema) |
| [`supplemental`](../../prompts/supplemental.yaml) | [`src/extraction/supplemental.py`](../../src/extraction/supplemental.py:493) (`get_system_prompt("supplemental")`) — endnote/footnote citation classification + extraction | `event_id`, `event_title`, `sub_event_id`, `sub_event_summary`, `text`, `endnote_block`, `endnote_refs`, `footnote_refs`, `ref_type_hint` | JSON citations (schema) |
| [`supplemental_narrative`](../../prompts/supplemental_narrative.yaml) | [`src/extraction/supplemental.py`](../../src/extraction/supplemental.py) — narrative-content footnote analysis | `event_id`, `event_name`, `footnote_text` | JSON narrative (schema) |
| [`source_section`](../../prompts/source_section.yaml) | [`src/extraction/source_section.py`](../../src/extraction/source_section.py:219) (`get_system_prompt("source_section")`) — operation/section anchor derivation per event | `book`, `series`, `section_title`, `event_name`, `sub_event_summaries`, `dates`, `places` | JSON section (schema) |

---

## Phase 2 — Maps & images (vision + search)

| Prompt | Invoked in (context) | Context vars | Output |
|--------|----------------------|--------------|--------|
| [`map_search`](../../prompts/map_search.yaml) | [`src/extraction/grok_search_maps.py`](../../src/extraction/grok_search_maps.py) — Grok-assisted historical map search | `place_name`, `date`, `event_context` | JSON map hits (schema) |
| [`map_vision`](../../prompts/map_vision.yaml) | [`src/extraction/grok_search_maps.py`](../../src/extraction/grok_search_maps.py) — vision verify a candidate is a relevant WWII map | `title`, `place_name`, `date`, `event_context` | JSON verdict (schema) |
| [`license_check`](../../prompts/license_check.yaml) | [`src/extraction/openserp_maps.py`](../../src/extraction/openserp_maps.py:119) `render_prompt("license_check")` — analyze a page's terms for image-use permission | `url`, `page_content` | JSON license (schema) |
| [`equipment_vision`](../../prompts/equipment_vision.yaml) | [`src/extraction/equipment.py`](../../src/extraction/equipment.py) — vision verify an image is the equipment | `equipment_name`, `equipment_category`, `image_title` | JSON verdict (schema) |
| [`equipment_urls`](../../prompts/equipment_urls.yaml) | [`src/extraction/equipment.py`](../../src/extraction/equipment.py) — pull image URLs from a Wikipedia/Commons page | `equipment_name`, `page_content` | JSON URLs (schema) |
| [`equipment_enrichment`](../../prompts/equipment_enrichment.yaml) | [`src/extraction/equipment.py`](../../src/extraction/equipment.py) — technical-spec enrichment | `common_name`, `category`, `country`, `identifier` | JSON specs (schema) |
| [`equipment_disambiguation`](../../prompts/equipment_disambiguation.yaml) | [`src/extraction/equipment_disambiguation.py`](../../src/extraction/equipment_disambiguation.py:191) `render_prompt("equipment_disambiguation")` — resolve a raw designation to a canonical item | `designation` | JSON canonical (schema) |

---

## Phase 3 — Enrichment (people, bibliography, publications)

These run inside `phase3_enrich_data.py` → `src/enrichment/*` and the supplemental-advanced path.

| Prompt | Invoked in (context) | Context vars | Output |
|--------|----------------------|--------------|--------|
| [`biography`](../../prompts/biography.yaml) | [`src/extraction/enrich_biographies.py`](../../src/extraction/enrich_biographies.py) — biographical-detail extraction from a source page | `person_name`, `source_name`, `text` | JSON bio (schema) |
| [`people_consolidation`](../../prompts/people_consolidation.yaml) | [`src/extraction/people_consolidation.py`](../../src/extraction/people_consolidation.py) — dedup candidate person entries | `entries` | JSON merge decision (schema) |
| [`bibliography_verify`](../../prompts/bibliography_verify.yaml) | [`src/enrichment/bibliography_resolver.py`](../../src/enrichment/bibliography_resolver.py:959) `render_prompt("bibliography_verify")` — verify a search result matches a citation (wraps `verify_prompt`) | `verify_prompt` | `YES`/`NO` |
| [`bibliography_alt_title`](../../prompts/bibliography_alt_title.yaml) | [`src/extraction/supplemental_advanced.py`](../../src/extraction/supplemental_advanced.py:449) `render_prompt("bibliography_alt_title")` — expand Green-Book citation shorthand | `verbatim` | expanded title |
| [`nara_identify`](../../prompts/nara_identify.yaml) | **Not used by the pipeline** — in [`src/enrichment/bibliography_resolver.py`](../../src/enrichment/bibliography_resolver.py) NARA record-group ID is DETERMINISTIC (`_identify_nara_record` → `_lookup_record_group`, an RG alias table; `grok_client` accepted but unused). The prompt IS invoked by the dev tool [`scripts/debug_nara_search.py`](../../scripts/debug_nara_search.py) (`render_prompt("nara_identify")`) to A/B the Grok RG approach against the deterministic one — so it is a live debug dependency, kept. | `verbatim` | RG label (debug only) |
| [`nara_verify`](../../prompts/nara_verify.yaml) | [`src/enrichment/bibliography_resolver.py`](../../src/enrichment/bibliography_resolver.py) — strict verify a NARA catalog record IS the cited document | `citation`, `nara_title`, `nara_description` | `YES`/`NO` |
| [`publication_search`](../../prompts/publication_search.yaml) | [`src/extraction/supplemental_search.py`](../../src/extraction/supplemental_search.py) — LLM search for an online copy of a publication | `title`, `author`, `doc_type` | URL or `NOT_FOUND` |
| [`isbn_lookup`](../../prompts/isbn_lookup.yaml) | [`src/extraction/supplemental_advanced.py`](../../src/extraction/supplemental_advanced.py) — ISBN lookup for a book | `title`, `author`, `publisher`, `pub_date` | JSON ISBN (schema) |
| [`author_death_date`](../../prompts/author_death_date.yaml) | [`src/extraction/copyright_calculator.py`](../../src/extraction/copyright_calculator.py) + [`supplemental_advanced.py`](../../src/extraction/supplemental_advanced.py) — author death date for copyright calc | `author` | JSON dates (schema) |
| [`date_summary`](../../prompts/date_summary.yaml) | [`src/extraction/date_summary.py`](../../src/extraction/date_summary.py) — summarize what a date is about from its mentions | `date_label`, `mentions` | JSON summary (schema) |

---

## Phase 0 — Ingestion (language / translation)

| Prompt | Invoked in (context) | Context vars | Output |
|--------|----------------------|--------------|--------|
| [`language_detect`](../../prompts/language_detect.yaml) | [`src/ingestion/translation.py`](../../src/ingestion/translation.py:82) `render_prompt("language_detect")` — detect a document's primary language | `sample` | ISO language code |
| [`translation`](../../prompts/translation.yaml) | [`src/ingestion/translation.py`](../../src/ingestion/translation.py:131) (`get_system_prompt("translation")`) — translate non-English markdown to English | `source_language`, `markdown` | translated markdown |

---

## Notes on the invocation contract

- **Single-pass rendering.** `render_prompt` substitutes each `{var}` exactly once
  (`src/utils/prompt_loader.py`), so values containing brace-like text are not
  re-interpreted (injection-safe). Unfilled `{lowercase}` placeholders are detected by
  `grok_client._validate_prompt`.
- **System vs user prompt.** All 31 prompts carry a `system_prompt` (set via
  `get_system_prompt`) establishing the WWII-domain role; the `prompt_template` is the
  user message with the interpolated context above.
- **Structured output.** Prompts with a `schema` block inject it as `{schema}` and the
  caller validates the JSON response against the matching entity schema in
  [`src/schemas/`](../../src/schemas/). The non-schema prompts return a short string
  (`YES`/`NO`, a URL, a language code, or free text).
- **Batch vs live.** In batch mode (`phase2`), extractor calls are *collected* under their
  `cache_type` lane and submitted to the Grok Batch API in two waves (events, then the
  per-event "optional" entities); in live mode the same prompts are called real-time.
