# People Deduplication

**Last Updated:** 2026-11-16

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

Dedup runs at the end of Phase 2 (`ecs_entrypoint._run_dedup_detection`), then blocks Phase 3 at the dedup gate until a human has reviewed the candidates in the Dedup Review UI.

### Step 1: Find Duplicates

```bash
python3 scripts/find_duplicate_people.py     # wrapper for: python3 -m src.dedup.find_duplicate_people
```

Scans all person files in `output/people/` and writes `output/people/duplicate_report.json`: grouped candidates with a score and match reasons (candidates above `dedup.people.candidate_threshold`, 0.5; see [dedup-weighting.md](dedup-weighting.md)).
Groups whose names are byte-identical after normalization are merged automatically (`_auto_merge_exact_duplicates`); everything else goes to review.

### Step 2: Review and merge (Dedup Review UI)

The Dedup Review UI (`lambda_handlers/dedup_ui_handler.py`, `POST /dedup/api/action`) shows each group; the reviewer chooses merge, skip, or exclude ("Not Duplicates"). Before each merge the handler writes pre-merge snapshots to `dedup/history/<snapshot_id>/` (`_save_merge_snapshot`) so `POST /dedup/api/undo` can restore them. Merging (`src/dedup/merge.py`) combines event mentions with the shared
mention rule (`entity_records.merge_mentions`), updates `output/people/index.json`, backs up and deletes the merged files. The interactive
`scripts/merge_duplicate_people.py` CLI no longer exists.

## Detection Methods

Scoring is additive (`_score_pair` in `src/dedup/find_duplicate_people.py`):

| Method | Description |
|--------|-------------|
| **Name similarity** | `SequenceMatcher` fuzzy match on original and normalized names |
| **ASCII/Unicode variants** | Handles accented characters (e.g., "Müller" vs "Mueller") |
| **Substring matching** | "Eisenhower" found within "Dwight D. Eisenhower" |
| **Shared biographical data** | Same birth date, nationality + birth year |
| **Same last name** | "George Patton" vs "George S. Patton" |
| **Mention proximity** | Same surname close together in the source text is strong evidence |
| **Shared external URL** | Same Wikipedia / Grokipedia URL, very high weight |
| **Vetoes** | Conflicting middle initial, or different full surnames, with a large negative weight |

There is no Grok verification step in the detector (the older "Grok verification" row was removed).

### Name Normalization

Names are normalized before comparison (`src/dedup/find_duplicate_people.py`: `_normalize_unicode`, `_normalize_german`; exclusion keys use `exclusions._normalize_exclusion_name`):
- ASCII-folds Unicode characters (e.g., ö → o, é → e); German transliteration handled by `_normalize_german`
- Lowercases, strips periods and commas, collapses whitespace (exclusion-key normalization)

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
Pairs marked "Not Duplicates" in the Dedup Review UI are stored in `output/people/not_duplicates.json`. These pairs are skipped in future duplicate detection runs. Tied to specific file IDs — if a file is recreated with a new ID, the exclusion is lost.

### Name-based exclusions (DynamoDB / persistent)
Stored as `name_exclusion#{type}#{name1}#{name2}` entries in DynamoDB (AWS) or local JSON (local mode). These survive file recreation because they match on normalized names rather than file IDs.

**Format:** `name_exclusion#people#eisenhower#eisenhower dwight d`

The Dedup Review UI writes **both** filename-based and name-based exclusions when "Not Duplicates" is selected, ensuring persistence regardless of whether files are later regenerated.

## Preventing false positives — the exclusion list

Detection can flag genuinely-different people who share a surname/substring (e.g. "Ray W. Barker"
vs "General Barker"). Confirmed non-duplicates are recorded so they never resurface:

- **`output/people/not_duplicates.json`** — a persistent list of excluded pairs
  (`{person1, person2}` filenames). Matching is bidirectional (order-independent) and survives
  across runs; future detection skips excluded pairs automatically.
- **Name-based exclusion** — the Dedup Review UI writes BOTH a filename-based and a name-based
  exclusion (`name_exclusion#people#<normalized name A>#<normalized name B>`, names sorted; `exclusions._make_name_pair_key`) when "Not Duplicates" is selected,
  so the exclusion persists even if the files are later regenerated.
- **How to exclude** — in the Dedup Review UI, mark a group "Not Duplicates" (the old
  `merge_duplicate_people.py` CLI is gone). `not_duplicates.json` can also be hand-edited.

## See Also

- [People Extraction](README.md) — file-per-person architecture and schema
- [People Groups](../people_groups/groups.md) — military unit linking

---

## Related
- [Weighted Scoring & Surname Suggestion Report](dedup-weighting.md) — config-driven
  proximity/rarity/rank scoring subtleties + the growth-triggered, human-gated surname
  frequency suggestion report (skew-safe, suggestion-only).

See also: [Dedup review gate (Phase 2 to Phase 3)](../../core/DEDUP_REVIEW_GATE.md) for detection, auto-merge, the gate, the review UI and the operator runbook.
