# TODO: Per-Book Cache Structure

**Priority:** Low  
**Created:** 2026-03-16

## Problem

All books share flat cache databases (`cache/api/{events,dates,places,peoplegroups}/`). Can't easily clear one book's cache without affecting others.

## Proposed Structure

```
cache/
├── books/
│   ├── BreakoutAndPursuit/
│   │   └── events/          # Event extraction (book-specific prompts)
│   └── CrossChannelAttack/
│       └── events/
└── global/
    ├── dates/                # Shared — same date across books
    ├── places/               # Shared — same place across books
    ├── people/               # Shared — same person across books
    ├── peoplegroups/         # Shared
    └── enrichment/           # Phase 3 (global)
```

## Rationale

- Events are book-specific (prompt includes chapter text) → per-book cache
- Dates, places, people are cross-book entities that get deduplicated → shared global cache avoids redundant API calls
- Phase 3 enrichment is entirely global (operates on `output/people/`)
- Per-book caches allow independent processing and targeted cache clearing

## Impact

- `GrokClient` cache path logic
- `phase2_extract.py` needs to pass book name to cache
- `config.yaml` cache paths
- Cache clearing commands in docs/README
