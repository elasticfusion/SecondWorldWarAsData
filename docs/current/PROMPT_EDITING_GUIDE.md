# Prompt Editing Guide

How to customize the AI extraction prompts used by the pipeline.

**Last Updated:** 2026-11-17 (checked against main 0dac86fa)

---

## Overview

LLM prompts are externalized to YAML files. A few Phase 3 helpers still keep short inline prompt text (see [Prompt Management](core/PROMPT_MANAGEMENT.md#known-issues)); new prompts belong in `prompts/`. If a YAML file is missing, `load_prompt` raises `FileNotFoundError` (no silent fallback). `scripts/deploy_all.sh` checks at build time that every required prompt file exists, has a `prompt_template`, and has a `schema` that parses as JSON.

**Prompt files:** `prompts/*.yaml` (34 files; full catalog with invoking code in [PROMPT_REFERENCE.md](PROMPT_REFERENCE.md))  
**Search query files:** `search_queries/*.yaml` (8 files: bibliography, equipment, events, maps, nara, people, people_groups, places)

### Extraction Prompts

| File | What it extracts |
|------|-----------------|
| `events.yaml` | Battles, operations, sub-events |
| `dates.yaml` | Temporal mentions (exact and approximate) |
| `places.yaml` | Geographic locations with coordinates |
| `people.yaml` | Biographical profiles |
| `people_groups.yaml` | Military units, organizations |
| `equipment_extract_broad.yaml` | Broad equipment discovery (replaces the old single `equipment.yaml`) |
| `equipment_refine.yaml` | Per-item equipment refinement (lineage fields) |
| `equipment_vessel_choice.yaml` | Pick the named vessel by date |
| `equipment_disambiguation.yaml` | Resolve a raw designation to a canonical item |
| `book_metadata.yaml` | Phase 0: book metadata with evidence quotes |
| `language_detect.yaml`, `translation.yaml` | Phase 0: language detection and translation to English |
| `weather_batch.yaml` | Weather extraction (batched; the live weather prompt) |
| `source_section.yaml` | Operation/section anchor per event |
| `date_summary.yaml` | Summary of what a date is about, from its mentions |
| `chart_vision.yaml` | Read the commands/units a chart image depicts |
| `casualties.yaml` | Personnel casualty data |
| `logistics.yaml` | Supply chain issues |
| `supplemental.yaml` | Bibliography/citation extraction |
| `biography.yaml` | Phase 3 biographical enrichment |

### Verification/Utility Prompts

| File | Purpose |
|------|---------|
| `equipment_vision.yaml` | Image relevance verification |
| `equipment_urls.yaml` | Wiki image URL extraction |
| `equipment_enrichment.yaml` | Equipment specs lookup |
| `map_search.yaml` | Map search query generation |
| `map_vision.yaml` | Map image verification |
| `license_check.yaml` | License/copyright analysis |
| `isbn_lookup.yaml` | ISBN number lookup |
| `author_death_date.yaml` | Author death date lookup |
| `publication_search.yaml` | Online publication search |
| `bibliography_verify.yaml` | Search result relevance (YES/NO) |
| `nara_identify.yaml` | NARA Record Group identification |
| `nara_verify.yaml` | NARA catalog match verification |
| `people_consolidation.yaml` | Duplicate person detection |

### Search Query Templates

| File | Used by |
|------|---------|
| `search_queries/people.yaml` | OpenSERP portrait and web-result searches (its `academic_sources` queries are unused: `search_academic_sources` has no caller) |
| `search_queries/equipment.yaml` | Equipment image/wiki searches |
| `search_queries/events.yaml` | Primary source searches |
| `search_queries/bibliography.yaml` | Gutenberg/Archive.org searches |
| `search_queries/maps.yaml` | External map searches |
| `search_queries/nara.yaml` | NARA catalog searches |
| `search_queries/people_groups.yaml`, `search_queries/places.yaml` | Group and place searches |

---

## Quick Start

### Edit a prompt locally

```bash
# Open the people extraction prompt
vim prompts/people.yaml

# Run Phase 2 to test your changes
python3 phase2_extract.py
```

Changes take effect immediately on the next run — no rebuild needed for local mode.

### Edit a prompt in AWS (no container rebuild)

```bash
# Upload the modified prompt to S3
aws s3 cp prompts/people.yaml s3://dev-wwii-data-pipeline/prompts/people.yaml --region us-east-1

# The next ECS task will use the S3 version automatically
```

The prompt loader checks S3 first, then falls back to the local file in the container. The S3 read happens only when `S3_BUCKET` is set and `aws.enabled` is true.

---

## Template Structure

Each YAML file has four sections:

```yaml
# 1. System prompt — sets the LLM's role
system_prompt: |
  You are a WWII historical data extraction assistant specializing in
  identifying people mentioned in military history texts.

# 2. Prompt template — the main instruction with {variables}
prompt_template: |
  Extract all people mentions from this WWII text.

  Source: {book} by {author} ({series})
  Event: {event_name} (ID: {event_id})
  Sub-event: {sub_event_summary} (ID: {sub_event_id})

  Text:
  {text}

  Return JSON matching this structure:
  {schema}

# 3. Schema — JSON example of expected output
schema: |
  {
    "People": [
      {
        "PersonID": "01ULID...",
        "name": "Dwight D. Eisenhower",
        ...
      }
    ]
  }

# 4. Rules — appended to the prompt as bullet points
rules:
  - Copy any id you are given exactly; never invent ids
  - If no people found, return empty array
  - Do NOT extract military units as people
```

---

## Available Variables

Variables are injected by the Python code. Use `{variable_name}` in your template.

### Common to most prompts

| Variable | Description | Example |
|----------|-------------|---------|
| `{text}` | Chapter/section text to extract from | Full paragraph text |
| `{event_name}` | Parent event name | "Operation Cobra" |
| `{event_id}` | Event ULID | "01KHXNSE0W..." |
| `{sub_event_summary}` | Sub-event description | "Breakout at St. Lô" |
| `{sub_event_id}` | Sub-event ULID | "01KHXNSE0WX..." |
| `{schema}` | Auto-injected from the `schema` field | JSON example |

### People and supplemental only

| Variable | Description |
|----------|-------------|
| `{book}` | Book title |
| `{author}` | Author name |
| `{series}` | Book series |

### Events only

| Variable | Description |
|----------|-------------|
| `{chapter}` | Chapter title |
| `{paragraphs}` | All paragraph text |
| `{images_text}` | Image descriptions |
| `{maps_text}` | Map descriptions |

### Weather only

| Variable | Description |
|----------|-------------|
| `{sub_event_blocks}` | One text block per sub-event; each block already carries that sub-event's places and dates for cross-referencing (built by `_build_places_section` / `_build_dates_section` in `src/extraction/weather_central.py`) |
| `{quote_rule}` | Shared quote rule, filled by default (`render_prompt`) |

### Casualties only

| Variable | Description |
|----------|-------------|
| `{entity_context}` | Available entity names |
| `{sub_event_block}` | Multiple sub-event texts keyed by ID |

### Logistics only

| Variable | Description |
|----------|-------------|
| `{sub_event_block}` | Multiple sub-event texts keyed by ID |

### Equipment only

| Variable | Description |
|----------|-------------|
| `{event_data}` | Full event JSON data |

### Supplemental only

| Variable | Description |
|----------|-------------|
| `{event_title}` | Event title |
| `{endnote_refs}` | Endnote reference numbers |
| `{footnote_refs}` | Footnote reference numbers |
| `{endnote_block}` | Endnote content text |
| `{ref_type_hint}` | Hint about reference type |

---

## Editing Tips

### Improving extraction quality

Add specific instructions to the `rules` section:

```yaml
rules:
  - Extract ranks with dates when mentioned
  - Always include nationality using ISO 3166-1 alpha-3 codes
  - Do NOT extract military units as people
  - Entries beginning with numbers or Roman numerals are military units
```

### Changing the output format

Edit the `schema` section to add or remove fields:

```yaml
schema: |
  {
    "People": [
      {
        "PersonID": "01ULID...",
        "name": "Full Name",
        "rank": "General",
        "nationality": "USA",
        "new_field": "your new field here"
      }
    ]
  }
```

**Important:** If you add fields to the schema, the downstream code must also handle them. New fields will be extracted but ignored unless the Python code processes them.

### Reducing misclassification

Add exclusion rules:

```yaml
rules:
  - Do NOT extract military units as people
  - Entries beginning with numbers (1st, 2nd), Roman numerals (I, II, VII), or number words (First, Second) are military units
  - "Examples to EXCLUDE: 1st Infantry Division, VII Corps, Third Army"
```

### Reducing costs

Shorter prompts use fewer input tokens. To reduce costs:
- Remove verbose examples from the schema (keep one concise example)
- Shorten the `prompt_template` instructions
- Remove unnecessary rules

---

## Testing Changes

### Local testing

```bash
# Test with a single chapter
python3 phase2_extract.py --book BookName --expected-count 1

# Check the output
cat output/content/BookName/chapter1-event.json | python3 -m json.tool
```

### AWS testing

```bash
# Upload modified prompt
aws s3 cp prompts/people.yaml s3://dev-wwii-data-pipeline/prompts/people.yaml --region us-east-1

# The cache key includes the prompt text, so an edited prompt is not served from old cache entries.
# To drop a finished book's other cache entries, keeping only its expensive 'events' cache:
python3 scripts/cleanup_book_cache.py BookName --dry-run

# Trigger a pipeline run
aws lambda invoke --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "book": "BookName", "phase": "2"}' \
  --cli-binary-format raw-in-base64-out --region us-east-1 /tmp/out.json
```

### Reverting to defaults

Delete the S3 override to revert to the container's built-in prompt:

```bash
aws s3 rm s3://dev-wwii-data-pipeline/prompts/people.yaml --region us-east-1
```

---

## How It Works

```
Extraction module calls render_prompt("people", book=..., text=...)
    ↓
prompt_loader checks S3: s3://bucket/prompts/people.yaml
    ↓ found? use it
    ↓ not found?
prompt_loader reads local: prompts/people.yaml
    ↓ found? use it
    ↓ not found?
FileNotFoundError (no fallback)
```

The S3 step is skipped unless `aws.enabled` and `S3_BUCKET` are set; any S3 error also falls through to the local file. The loader caches templates in memory (`lru_cache`) for the duration of the ECS task. To force a reload after S3 changes mid-run, the cache would need to be cleared programmatically (not typically needed since each ECS task is short-lived).

---

## Reference

- **Prompt loader code:** `src/utils/prompt_loader.py`
- **Configuration:** [Configuration Guide — Prompt Templates](core/CONFIGURATION.md#prompt-templates)
- **Known Issues:** [Data Quality Status — Known Issues](DATA_QUALITY_STATUS.md#known-data-quality-issues)
- **Schema Reference:** [JSON output format](SCHEMA_REFERENCE.md)
