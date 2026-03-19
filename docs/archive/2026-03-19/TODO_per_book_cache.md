# TODO: Per-Book Cache Structure

**Priority:** Low  
**Status:** ✅ Done (2026-03-16)  
**Created:** 2026-03-16

---

## Summary

Book-specific cache types (events, weather, equipment, logistics, casualties, supplemental) now route to `cache/api/books/{BookName}/{type}/` when a book context is set. Global types (dates, places, people, peoplegroups) always use `cache/api/{type}/`.

## Implementation

- `src/grok_client.py`: `current_book` context variable (thread/async safe via `contextvars`), `BOOK_CACHE_TYPES` set, `_get_cache()` routes based on `current_book.get()`
- `src/extraction/batch_parallel.py`: sets `current_book` in `process_chapter_async()` before extraction
- `phase2_extract.py`: sets `current_book` before optional entity extraction and retry loops

## Cache Structure

```
cache/api/
├── books/
│   ├── BreakoutAndPursuit/
│   │   ├── events/          # Book-specific
│   │   ├── weather/
│   │   └── supplemental/
│   └── CrossChannelAttack/
│       └── events/
├── dates/                    # Global (shared across books)
├── places/
├── people/
└── peoplegroups/
```

## Clearing Per-Book Cache

```bash
rm -rf cache/api/books/BreakoutAndPursuit/events/*
```
