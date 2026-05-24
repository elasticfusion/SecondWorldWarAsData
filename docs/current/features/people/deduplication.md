# People Deduplication

**Last Updated:** 2026-05-23

## Problem

Historical texts reference people inconsistently — by full name, last name only, title, rank, or nickname. This creates duplicate person files in `output/people/`.

## Architecture

Each person is stored as an individual JSON file in `output/people/`. Deduplication operates on these files directly — no central file is needed.

Key files:

| File | Purpose |
|------|---------|
| `output/people/*.json` | Individual person files |
| `output/people/duplicate_report.json` | Generated duplicate analysis |
| `output/people/not_duplicates.json` | Exclusion list (confirmed non-duplicates) |

## Workflow

### Step 1: Find Duplicates

```bash
python3 scripts/find_duplicate_people.py
```

Scans all person files in `output/people/` and generates `output/people/duplicate_report.json` containing grouped duplicate candidates with confidence scores and match reasons.

### Step 2: Merge Duplicates

```bash
python3 scripts/merge_duplicate_people.py
```

Interactive merge — for each duplicate group, prompts with:
- **y** — merge into the primary person
- **n** — skip this group
- **skip** — skip this pair
- **exclude** — add to `output/people/not_duplicates.json` (never flagged again)

Merging combines event mentions, updates `output/people/index.json`, and deletes the merged files.

## Detection Methods

| Method | Description |
|--------|-------------|
| **Name similarity** | SequenceMatcher fuzzy match at 70%+ threshold |
| **ASCII/Unicode variants** | Handles accented characters (e.g., "Müller" vs "Mueller") |
| **Substring matching** | "Eisenhower" found within "Dwight D. Eisenhower" (>5 chars) |
| **Shared biographical data** | Same birth date, nationality + birth year |
| **Same last name** | "George Patton" vs "George S. Patton" (>3 char last names) |
| **Grok verification** | Ambiguous single-name matches verified by Grok AI before flagging |

### Name Normalization

`normalize_name()` applies aggressive normalization before comparison:
- Strips periods and commas
- ASCII-folds Unicode characters (e.g., ö → o, é → e)
- Lowercases
- Collapses whitespace

## Duplicate Report Format

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

## Exclusion List

Two types of exclusion prevent false positives from recurring:

### Filename-based exclusions
Pairs added via the `exclude` option during interactive merge are stored in `output/people/not_duplicates.json`. These pairs are skipped in future duplicate detection runs. Tied to specific file IDs — if a file is recreated with a new ID, the exclusion is lost.

### Name-based exclusions (DynamoDB / persistent)
Stored as `name_exclusion#{type}#{name1}#{name2}` entries in DynamoDB (AWS) or local JSON (local mode). These survive file recreation because they match on normalized names rather than file IDs.

**Format:** `name_exclusion#people#eisenhower#eisenhower dwight d`

The Dedup Review UI writes **both** filename-based and name-based exclusions when "Not Duplicates" is selected, ensuring persistence regardless of whether files are later regenerated.

### Processed Events Registry

The dedup scripts track which event files have been processed in `.processed_events.json`. This uses **basename** (not absolute path) to ensure portability between local and AWS environments.

## See Also

- [People Extraction](README.md) — file-per-person architecture and schema
- [People Groups](groups.md) — military unit linking
