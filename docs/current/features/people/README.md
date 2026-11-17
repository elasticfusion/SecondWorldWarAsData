# File-Per-Person Implementation

**Last Updated:** 2026-11-16

## How people are extracted (Phase 2)

- People are discovered over fixed 3-paragraph windows of the raw parsed text (`src/extraction/windows.py`), one request per window
  (`batch_parallel._extract_people`, `cache_type "people"`). The prompt asks for name (no rank), rank, unit, country, `position_at_event`,
  `life_event` and a verbatim `original_text`.
- Each item is settled against the source (`windows.settle`): a quote that is not in the window is dropped (`quote_unsupported`); the item is attributed to the
  real sub-event whose paragraphs hold the quote.
- Not-a-person filter: staff sections and units (`VIII Corps G-3`) are dropped (`batch_parallel._is_not_a_person`); ranks are stripped from the name (`batch_parallel._strip_rank`).
- `PersonID` and every `MentionID` are minted by code (`people._mint_person_ids`); a model-supplied value is replaced.
- Find-or-create is by normalized name (`people._normalize_name`); a repeat mention is merged by `entity_records.mention_identity` (same sub-event + same normalized quote = one mention).
- Phase 2 also looks each new person up in Grokipedia/Wikipedia (`enrich_biographies.enrich_person_from_sources`, called from `people._enrich_person_phase2` /
  `_enrich_person_batch_apply`), so `biographical_profile` can already be present at the end of Phase 2. Phase 3 adds only OpenSERP results (end of this page).

## Structure

```
output/people/
├── index.json                                    # name → filename lookup
├── Dwight_D_Eisenhower_01ABC123.json            # Individual person files
├── George_S_Patton_01DEF456.json
├── Bernard_Montgomery_01GHI789.json
├── Supreme_Commander_AEEF_01JKL012.json         # Title-based (potential duplicate)
├── Eisenhower_01MNO345.json                     # Last name only (potential duplicate)
└── duplicate_report.json                         # Duplicate analysis (written by dedup, end of Phase 2)

```

## Person File Format

Each file contains a single person:

```json
{
  "PersonID": "01ULID...",
  "name": "Dwight D. Eisenhower",
  "source_language": "English",
  "biographical_profile": {
    "birth_date": "1890-10-14",
    "death_date": "1969-03-28",
    "nationality": "American",
    "biographical_details": "...",
    "ranks": [{ "rank": "General", "branch": "US Army", "date": "1945-03-12" }],
    "units_served": [{ "unit": "SHAEF", "from": "1944-01", "to": "1945-05" }],
    "military_awards": [{ "award": "...", "class": null, "date_awarded": null }],
    "biography_sources": [{ "source": "Wikipedia", "confidence": 0.9, "fields_sourced": ["birth_date"], "page": null }]
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "The Allies",
      "EventID": "01ULID...",
      "Sub_event_Name": "...",
      "Sub_eventID": "01ULID...",
      "book": "Breakout and Pursuit",
      "author": "Martin Blumenson",
      "series": "United States Army in World War II",
      "date": "1944-06-06",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

**Notes:**
- `biographical_profile` is populated by the Phase-2 Grokipedia/Wikipedia lookup (absent when it found no source); Phase 3 OpenSERP adds portrait images (`images[]`) and verified web pages (stored in `military_awards[]`)
- The filename is `{name, special characters removed, max 50 chars}_{first 12 chars of PersonID}.json` (`people._name_to_filename`)
- Mentions carry `original_text` (the verbatim quote), `position_at_event` and `life_event` as well; `MentionID` is minted by code
- `DateMentionID` → top-level `DateID` in `output/dates/*.json` (resolved via lookup, not LLM-provided)
- `Sub_event_Name` uses underscore (not hyphen)

## Index File

Fast lookup by normalized name:

```json
{
  "dwight d eisenhower": "Dwight_D_Eisenhower_01ABC123.json",
  "george s patton": "George_S_Patton_01DEF456.json",
  "eisenhower": "Eisenhower_01MNO345.json"
}
```

## Duplicate Detection

Detection is scored, config-driven and documented in [deduplication.md](deduplication.md) and [dedup-weighting.md](dedup-weighting.md)
(`src/dedup/find_duplicate_people.py`, `config.yaml` `dedup.people`). The 40/30/50% weights and the 60% threshold that were listed here were the
early heuristic; current scoring is additive with `candidate_threshold: 0.5`, mention-proximity weights, and vetoes for conflicting middle initials or different surnames.

### Duplicate Report Format:

```json
{
  "total_people": 1247,
  "duplicate_groups": 23,
  "duplicates": [
    {
      "confidence": 0.95,
      "reasons": [
        "Name similarity: 0.92",
        "Same last name: eisenhower",
        "Shared positions"
      ],
      "people": [
        {
          "filename": "Dwight_D_Eisenhower_01ABC123.json",
          "name": "Dwight D. Eisenhower",
          "PersonID": "01ABC123..."
        },
        {
          "filename": "Eisenhower_01MNO345.json",
          "name": "Eisenhower",
          "PersonID": "01MNO345..."
        }
      ]
    }
  ]
}
```

## Workflow

### 1. Extraction (Automatic)
```bash
python phase2_extract.py
```
- Creates individual person files
- Updates index.json
- Dedup at the end of the phase generates duplicate_report.json

### 2. Review Duplicates (Manual)
```bash
cat output/people/duplicate_report.json
```
- Review high-confidence matches
- Verify they're actually the same person

### 3. Merge Duplicates (Dedup Review UI)
Groups are merged or excluded in the Dedup Review UI, which blocks Phase 3 until the review is done (`src/dedup/merge.py`).
The old `scripts/merge_duplicate_people.py` CLI no longer exists.
- Combines event mentions
- Updates index
- Deletes merged files (after a backup)

## Benefits

✅ **Incremental**: Process one chapter without loading all people  
✅ **Scalable**: Handles 10,000+ people efficiently  
✅ **Human-readable**: Direct file access by name  
✅ **Git-friendly**: Diffs show which people changed  
✅ **Parallel-safe**: Can process multiple chapters simultaneously  
✅ **Smart detection**: Multiple heuristics for finding duplicates  
✅ **Reviewable**: Confidence scores help prioritize manual review  

## Queries

### Find a person:
```bash
# By name
cat output/people/index.json | jq '.["dwight d eisenhower"]'

# Direct access
cat output/people/Dwight_D_Eisenhower_01ABC123.json
```

### List all people:
```bash
ls output/people/*.json | grep -v index | grep -v duplicate_report
```

### Count people:
```bash
ls output/people/*.json | grep -v index | grep -v duplicate_report | wc -l
```

### Find people at event:
```bash
grep -l "EventID.*01ABC123" output/people/*.json
```

---

> **People-groups (units & organizations) are a separate, first-class entity** — see
> [../people_groups/README.md](../people_groups/README.md). A person *belongs to* groups;
> the group is its own record.

## Enrichment

Grokipedia + Wikipedia biographical enrichment (references, portrait photos, award citations,
title-implied memberships) runs in **Phase 2**, inside the people extractor
(`enrich_biographies.enrich_person_from_sources`, see the top of this page). `enrich_all_people`
still exists for the module CLI and tests but `phase3_enrich_data.py` does not call it.
Phase 3 contributes only OpenSERP image and web results (step 5/6, when `supplemental_material.use_openserp`
is enabled), gated by the shared `enrichment_gate`. See
[Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md) and [biographical-enrichment.md](biographical-enrichment.md).
