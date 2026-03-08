# Single Central People File - Corrected

## Structure

```
output/
├── BreakoutAndPursuit/
│   ├── chapter1a-event.json
│   ├── chapter1a-dates.json
│   ├── chapter1a-places.json
│   └── ...
├── Cross-Channel-Attack/
│   ├── chapter0a-event.json
│   ├── chapter0a-dates.json
│   ├── chapter0a-places.json
│   └── ...
└── people/
    ├── people-central.json       ← ALL books, ALL people
    └── people-consolidated.json  ← Deduplicated
```

## Key Points

### ✅ Single File for ALL Books
- **Location**: `output/people/people-central.json`
- **Contains**: People from Breakout and Pursuit, Cross-Channel Attack, and any other books
- **Not**: One file per book

### ✅ Event Mentions Reference Books
Each `event_mention` includes:
```json
{
  "MentionID": "01...",
  "Event_Name": "The Allies",
  "EventID": "01...",
  "Sub-event_Name": "Allied progress by 1 July 1944...",
  "Sub-eventID": "01...",
  "book": "Breakout and Pursuit",
  "author": "Martin Blumenson",
  "series": "United States Army in World War II",
  "position_at_event": "Supreme Commander",
  "original_text": "..."
}
```

### ✅ Cross-Book Tracking
A person like Eisenhower will have ONE entry with event mentions from multiple books:
```json
{
  "PersonID": "01...",
  "name": "Dwight D. Eisenhower",
  "event_mentions": [
    {
      "book": "Breakout and Pursuit",
      "Event_Name": "The Allies",
      ...
    },
    {
      "book": "Cross-Channel Attack",
      "Event_Name": "Planning the Invasion",
      ...
    }
  ]
}
```

## How It Works

1. **First chapter processed**: Creates `output/people/people-central.json`
2. **Subsequent chapters**: Loads existing file, merges new people
3. **Across books**: Same file accumulates people from all books
4. **After all extractions**: Consolidates duplicates into `people-consolidated.json`

## Benefits

✅ **Single source of truth**: One file for entire corpus  
✅ **Cross-book references**: Track people across multiple books  
✅ **Complete history**: All event mentions in one place  
✅ **Easy queries**: "Show all Eisenhower mentions" = one lookup  
✅ **Proper ULIDs**: All events/sub-events linked via ULIDs  

## Cleanup and Run

```bash
./cleanup_people.sh
python phase2_extract.py
```

Output:
- `output/people/people-central.json` (raw, may have duplicates)
- `output/people/people-consolidated.json` (deduplicated, canonical)
