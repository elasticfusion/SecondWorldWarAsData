# Preventing False Positive Duplicates

## Problem
The duplicate detection algorithm may flag people as duplicates when they're actually different individuals (e.g., "Ray W. Barker" vs "General Barker" might be different people).

## Solution: Exclusion List

### File: `output/people/not_duplicates.json`

```json
{
  "comment": "Confirmed non-duplicates",
  "exclusions": [
    {
      "person1": "Ray_W_Barker_01KJ325R.json",
      "person2": "General_Barker_01KJ32CZ.json"
    }
  ]
}
```

## Usage

### During Merge Process

When running `python3 merge_duplicate_people.py`, you now have 4 options:

1. **`y`** - Merge this group (they ARE duplicates)
2. **`n`** - Stop the entire process
3. **`skip`** - Skip this group, continue to next
4. **`exclude`** - Mark as NOT duplicates (prevents future detection)

### Example Workflow

```
Duplicate Group (Confidence: 0.90)
Reasons: Same last name: barker, Name substring match
1. Ray W. Barker (Ray_W_Barker_01KJ325R.json)
2. General Barker (General_Barker_01KJ32CZ.json)

Merge this group? (y/n/skip/exclude): exclude
✓ Added to exclusion list (will not appear in future reports)
```

## How It Works

1. **User marks pair as non-duplicate** using `exclude` option
2. **Pair added to `not_duplicates.json`** with both filenames
3. **Future duplicate detection** skips this pair automatically
4. **Bidirectional matching** - works regardless of comparison order

## Benefits

✅ **Persistent** - Exclusions survive across multiple runs
✅ **Automatic** - No manual editing of JSON required
✅ **Accumulative** - Builds up over time as you review duplicates
✅ **Clean reports** - Future reports won't show false positives

## Manual Editing

You can also manually edit `not_duplicates.json`:

```json
{
  "comment": "Confirmed non-duplicates",
  "exclusions": [
    {
      "person1": "Person_A_01ABC.json",
      "person2": "Person_B_01DEF.json"
    },
    {
      "person1": "Person_C_01GHI.json",
      "person2": "Person_D_01JKL.json"
    }
  ]
}
```

## Quality Assurance

- ✅ **Pylint**: 10.0/10
- ✅ **Mypy**: No issues
- ✅ **Tested**: Exclusion logic verified

## Status: ✅ Implemented
