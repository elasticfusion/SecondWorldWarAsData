# Feature Documentation

**Last Updated:** 2026-03-13  
**Status:** Comprehensive feature documentation based on code analysis

---

## Overview

This directory contains comprehensive documentation for all extraction features in the WWII data pipeline. Each feature is documented based on actual code implementation.

---

## Core Extraction Features

### Events (`events.py`)
**Status:** Production  
**Documentation:** [events/README.md](events/README.md)

Hierarchical event extraction with sub-events, participants, and temporal/spatial context.

**Key Features:**
- Hierarchical event structure (events → sub-events)
- Participant extraction with roles
- Temporal and spatial context
- Equipment and casualty mentions
- Structured output with ULIDs

---

### Dates (`dates.py`)
**Status:** Production  
**Documentation:** [dates/README.md](dates/README.md)

Temporal entity extraction with central repository management.

**Key Features:**
- ISO 8601 date formatting
- Precision levels (exact, approximate, range)
- Central repository (one file per month)
- Event linkage via MentionID
- Deduplication by date value

---

### Places (`places.py`)
**Status:** Production  
**Documentation:** [places/README.md](places/README.md)

Geographic entity extraction with coordinates and central repository.

**Key Features:**
- GPS coordinates (latitude/longitude)
- Country codes (ISO 3166-1 alpha-3)
- Place types (city, region, country, etc.)
- Central repository (one file per place)
- Deduplication by name
- Map URL integration

---

### People (`people.py`)
**Status:** Production  
**Documentation:** [people/README.md](people/README.md)

Individual person extraction with biographical profiles.

**Key Features:**
- File-per-person storage
- Biographical profiles (rank, nationality, dates)
- Event mention tracking
- Deduplication system
- Source tracking
- Biographical enrichment (Wikipedia/Grokipedia)

---

### People Groups (`people_groups.py`)
**Status:** Production  
**Documentation:** [people/groups.md](people/groups.md)

Military units and organizations extraction.

**Key Features:**
- File-per-group storage
- Hierarchical relationships (parent/child units)
- Alternate names and aliases
- Country of origin
- Event mention tracking
- Deduplication system

---

## Supplemental Features

### Equipment (`equipment.py`)
**Status:** Experimental  
**Documentation:** [equipment/](equipment/)

Military equipment extraction with specifications and media.

**Key Features:**
- Equipment specifications
- Media integration (images, diagrams)
- Entity linking (people, places, events)
- Deduplication
- Wikipedia/Grokipedia enrichment
- Vision API verification

---

### Casualties (`casualties.py`)
**Status:** Experimental  
**Documentation:** [casualties/INTEGRATION.md](casualties/INTEGRATION.md)

Casualty data extraction from events.

**Key Features:**
- Casualty counts by type (killed, wounded, missing, captured)
- Side attribution (Allied, Axis, Civilian)
- Event linkage
- Structured output

---

### Logistics (`logistics.py`)
**Status:** Experimental  
**Documentation:** [logistics/README.md](logistics/README.md)

Supply chain and logistics extraction.

**Key Features:**
- Supply types and quantities
- Transport methods
- Routes and destinations
- Delays and issues
- Event linkage

---

### Weather (`weather_central.py`)
**Status:** Optional  
**Documentation:** [weather/README.md](weather/README.md)

Weather conditions extraction with API integration.

**Key Features:**
- Historical weather data (Open-Meteo API)
- Temperature, precipitation, conditions
- Central repository
- Date and place linkage
- API response caching

---

## Maps Features

### Source Maps (`maps.py`)
**Status:** Production  
**Documentation:** [maps/README.md](maps/README.md)

Map extraction from source documents.

**Key Features:**
- Map metadata extraction
- Image download from source URLs
- S3 storage support
- Map classification (tactical, strategic, logistical)
- Date and place linkage

---

### External Maps (`external_maps.py`, `search_external_maps.py`)
**Status:** Optional  
**Documentation:** [external-maps/](external-maps/)

Third-party map search and verification.

**Key Features:**
- Multi-source search (OpenSERP, Grok)
- Vision API verification
- License compliance
- Domain blacklist/whitelist
- Search history tracking
- Image processing and storage

---

## Supplemental Materials

### Supplemental Extraction (`supplemental.py`)
**Status:** Production  
**Documentation:** [supplemental/](supplemental/)

Bibliography and reference extraction.

**Key Features:**
- Citation parsing (books, articles, archives)
- ISBN extraction (post-1966)
- Copyright determination
- URL validation
- Search integration (Gutenberg, Archive.org, OpenSERP)
- Availability classification

---

## Advanced Features

### Batch Processing (`batch_parallel.py`)
**Status:** Production  
**Documentation:** [batch_processing/README.md](batch_processing/README.md)

Parallel and batch extraction for performance.

**Key Features:**
- Async/await parallelization
- Batch API calls
- Configurable concurrency limits
- Error handling per file
- Progress tracking

---

### Biographical Enrichment (`enrich_biographies.py`)
**Status:** Production  
**Documentation:** [people/biographical-enrichment.md](people/biographical-enrichment.md)

Wikipedia and Grokipedia integration for people.

**Key Features:**
- Wikipedia search and extraction
- Grokipedia fallback
- Birth/death dates
- Biographical summaries
- Caching
- Error handling (403, timeouts)

---

## Utility Modules

### Copyright Calculator (`copyright_calculator.py`)
**Status:** Production  
**Documentation:** [supplemental/SUPPLEMENTAL_PHASE2.md](supplemental/SUPPLEMENTAL_PHASE2.md)

Copyright status determination for supplemental materials.

**Key Features:**
- USA copyright law (pre-1928, 1928-1977, post-1977)
- EU/UK copyright law (life + 70 years)
- Government works handling
- Author death date lookup

---

### Search History (`search_history.py`)
**Status:** Production  
**Documentation:** [external-maps/search-history.md](external-maps/search-history.md)

Tracks external map searches to prevent duplicates.

**Key Features:**
- Per-place search tracking
- URL deduplication
- Search metadata (timestamp, source)
- JSON persistence

---

### URL Validation (`validate_supplemental_urls.py`)
**Status:** Production  
**Documentation:** [supplemental/SUPPLEMENTAL_VALIDATION.md](supplemental/SUPPLEMENTAL_VALIDATION.md)

Archive URL verification for supplemental materials.

**Key Features:**
- HEAD request verification
- Timeout handling
- Verification date tracking
- Batch processing

---

## Configuration

All features are configured via `config.yaml`. See [core/CONFIGURATION.md](../core/CONFIGURATION.md) for details.

---

## Feature Status Legend

- **Production:** Stable, actively used, well-tested
- **Experimental:** Working but may change, use with caution
- **Optional:** Disabled by default, enable in config

---

## Adding New Features

See [core/DEVELOPMENT.md](../core/DEVELOPMENT.md) for guidelines on adding new extraction features.

---

## Feature Dependencies

```
events.py (core)
├── dates.py
├── places.py
├── people.py
├── people_groups.py
├── equipment.py (optional)
├── casualties.py (optional)
├── logistics.py (optional)
└── weather_central.py (optional)

maps.py (independent)
external_maps.py (independent)
supplemental.py (independent)
```

---

## Performance Considerations

- **Parallel Processing:** `batch_parallel.py` processes chapters concurrently with batched API calls (default)
- **Caching:** All API responses cached in `cache/api/`
- **Auto-Recovery:** Corrupted cache entries cleared automatically on retry

---

## Error Handling

All extraction modules implement:
- Retry logic with exponential backoff
- JSON sanitization
- Input size validation
- File-specific error messages
- Cache clearing suggestions

See [core/error_handling.md](../core/error_handling.md) for details.
