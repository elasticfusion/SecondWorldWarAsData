# Cache Review - Place Data

**Date:** 2026-02-21  
**Question:** Is event data (fulltext) stored in the place cache?

## Answer: NO ✅

The place cache stores **only extracted place mentions**, not the source event text.

## Cache Structure

### Places Cache (`cache/api/places/cache.db`)
- **Format:** SQLite database with pickled Python objects
- **Entries:** 1 cached response
- **Data stored:**
  ```
  {
    "Event_Name": "The Allies",
    "EventID": "01H2J3K4M5N6P7T8V9ZABCDGHJ",
    "Sub_event_Name": "Strategic situation of Allied forces...",
    "Sub_eventID": "01KJ0F3GMQPZHXZ316VEF3NJDK",
    "Place_Mentions": [...]  // Array of place objects
  }
  ```

### Dates Cache (`cache/api/dates/cache.db`)
- **Entries:** 181 cached responses
- **Data stored:**
  ```
  {
    "Event_Name": "...",
    "EventID": "...",
    "Sub_event_Name": "...",
    "Sub_eventID": "...",
    "Date_Mentions": [...]
  }
  ```

### Events Cache (`cache/api/events/cache.db`)
- **Entries:** 0 (empty)

## What's NOT Stored

The cache does **NOT** contain:
- `Sub-event_fulltext` (original paragraph text)
- `Sub-event_summary`
- Paragraph numbers
- Source file references

## Cache Purpose

The cache stores **API responses only** - the extracted entities returned by Grok. This is efficient because:

1. **Avoids redundant API calls** - Same sub-event extraction won't call Grok twice
2. **Minimal storage** - Only stores the structured output, not input text
3. **Fast lookups** - SQLite with hash-based keys

## Source Data Location

The full event data (including `Sub-event_fulltext`) is stored in:
- `output/*/chapter*-event.json` - Event and sub-event definitions with full text
- `contentrepository/*/chapter*/chapter*-content.md` - Original markdown source

## Conclusion

✅ **Cache is properly designed** - stores only API responses, not source data  
✅ **No data duplication** - Event fulltext remains in event JSON files  
✅ **Efficient** - Minimal cache size, fast lookups
