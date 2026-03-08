# People Central Management Implementation

## Overview

Implemented central management for people extraction as specified in requirements. People are now tracked in a single central file that accumulates across all books and chapters.

## Changes Made

### 1. Central File Structure
- **Output**: Single `people-central.json` file instead of per-chapter files
- **Location**: `output/{BookName}/people-central.json`
- **Accumulation**: File grows as more chapters are processed

### 2. Deduplication Logic
- **Name Normalization**: Case-insensitive matching (`_normalize_name()`)
- **Index Lookup**: Fast O(1) lookup using normalized name index
- **Merge Strategy**: 
  - Existing person → append new event mentions
  - New person → add to central list

### 3. Biographical Profile Merging
- **Awards**: Deduplicate and accumulate military awards
- **Fields**: Fill missing biographical data from new mentions
- **Preservation**: Never overwrite existing data with null/empty values

### 4. Book/Source Tracking
Added to `PersonEventMention`:
- `book`: Book title (e.g., "Breakout and Pursuit")
- `author`: Author name (e.g., "Martin Blumenson")
- `series`: Series name (e.g., "United States Army in World War II")

### 5. Cross-Book References
Each person can have event mentions from multiple books:
```json
{
  "PersonID": "01H8XYZI1AB123CD456EF789GH",
  "name": "Dwight D. Eisenhower",
  "event_mentions": [
    {
      "book": "Breakout and Pursuit",
      "author": "Martin Blumenson",
      "Event_Name": "The Allies",
      ...
    },
    {
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "Event_Name": "Planning the Invasion",
      ...
    }
  ]
}
```

## Implementation Details

### Key Functions

**`_normalize_name(name: str) -> str`**
- Strips whitespace and converts to lowercase
- Enables consistent person matching

**`_merge_person(existing: Dict, new_person: Dict) -> Dict`**
- Appends new event mentions to existing person
- Merges biographical profiles intelligently
- Deduplicates military awards

**`extract_people()`**
- Loads existing central file (if exists)
- Builds name index for fast lookups
- Extracts book metadata from parsed file
- Processes each sub-event
- Merges or adds people to central list
- Saves updated central file

### Logging
- Reports existing people count on load
- Tracks new vs. updated people counts
- Logs final statistics: total, new, updated

## Benefits

1. **Single Source of Truth**: One file per book series with all people
2. **Complete Profiles**: Biographical data accumulates across mentions
3. **Cross-Reference**: Track individuals across multiple books/events
4. **Efficient**: O(1) lookup for deduplication
5. **Traceable**: Each mention includes book/author/series metadata

## Requirements Compliance

✅ **Central Management**: Single file, not per-chapter  
✅ **Append Logic**: New mentions added to existing people  
✅ **Cross-Book**: Supports multiple book references  
✅ **Event/Sub-event Links**: All ULIDs preserved  
✅ **Source Tracking**: Book, author, series in each mention  

## Usage

The pipeline automatically:
1. Creates `people-central.json` on first run
2. Loads and updates it for subsequent chapters
3. Merges people across all processed chapters
4. Maintains complete event mention history

No manual intervention required.
