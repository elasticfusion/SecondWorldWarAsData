# Prompt Editing Guide

How to customize the AI extraction prompts used by the pipeline.

**Last Updated:** 2026-04-26

---

## Overview

The pipeline uses YAML prompt templates to instruct the Grok LLM on what to extract from WWII texts. Each entity type has its own template. You can edit these to improve extraction quality, add rules, or change the output format — without modifying Python code.

**Prompt files:** `prompts/*.yaml`

| File | What it extracts |
|------|-----------------|
| `events.yaml` | Battles, operations, sub-events |
| `dates.yaml` | Temporal mentions (exact and approximate) |
| `places.yaml` | Geographic locations with coordinates |
| `people.yaml` | Biographical profiles |
| `equipment.yaml` | Weapons, vehicles, specifications |
| `weather.yaml` | Historical weather conditions |
| `logistics.yaml` | Supply chain issues |
| `casualties.yaml` | Casualty tracking |
| `supplemental.yaml` | Bibliography and citations |

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

The prompt loader checks S3 first, then falls back to the local file in the container.

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
  - Generate 26-character ULIDs
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
| `{places_section}` | Available places for cross-referencing |
| `{dates_section}` | Available dates for cross-referencing |

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
python3 phase2_extract.py --chapters chapter1

# Check the output
cat output/BookName/chapter1-event.json | python3 -m json.tool
```

### AWS testing

```bash
# Upload modified prompt
aws s3 cp prompts/people.yaml s3://dev-wwii-data-pipeline/prompts/people.yaml --region us-east-1

# Clear cache for the entity type to force re-extraction
# (otherwise cached results are returned)
python3 /tmp/clear_cache.py

# Trigger a pipeline run
aws s3 sync contentrepository/ s3://dev-wwii-data-pipeline/content/ --region us-east-1
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
Falls back to hardcoded f-string in Python (legacy)
```

The loader caches templates in memory for the duration of the ECS task. To force a reload after S3 changes mid-run, the cache would need to be cleared programmatically (not typically needed since each ECS task is short-lived).

---

## Reference

- **Prompt loader code:** `src/utils/prompt_loader.py`
- **Configuration:** [Configuration Guide — Prompt Templates](core/CONFIGURATION.md#prompt-templates)
- **Known Issues:** [Entity misclassification](KNOWN_ISSUES.md)
- **Schema Reference:** [JSON output format](SCHEMA_REFERENCE.md)
