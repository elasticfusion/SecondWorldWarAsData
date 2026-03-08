# Cache Structure

## Overview

The pipeline uses multiple cache types to minimize API calls and avoid re-downloading content.

## Cache Types

### 1. API Response Caches (Grok API)
**Location:** `cache/api/{type}/`

Caches Grok API responses by extraction type:

- **`cache/api/events/`** - Event/sub-event extraction responses
- **`cache/api/dates/`** - Date mention extraction responses
- **`cache/api/places/`** - Place mention extraction responses
- **`cache/api/people/`** - Person extraction responses
- **`cache/api/peoplegroups/`** - People group extraction responses
- **`cache/api/weather/`** - Weather mention extraction responses
- **`cache/api/supplemental/`** - Supplemental material extraction responses

**Purpose:** Avoid duplicate API calls for same content

**Cache Key:** SHA256 hash of (prompt + temperature + model)

**Behavior:**
- Only caches validated, successful responses
- Separate cache per extraction type prevents collisions
- Persistent across runs (disk-based)

### 2. Downloaded Content Caches
**Location:** `cache/{type}/`

Caches downloaded external resources:

- **`cache/images/`** - Downloaded images from external URLs
- **`cache/maps/`** - Downloaded map images

**Purpose:** Avoid re-downloading same images/maps

**Cache Key:** Filename based on URL hash or original filename

**Behavior:**
- Downloads once, reuses on subsequent runs
- Includes metadata (URL, download date, license info)

### 3. Central Data Files
**Location:** `output/`

Accumulated data across all chapters:

- **`output/people.json`** - Centrally managed people profiles
  - Appends event mentions to existing people
  - Prevents duplicate person records
  
- **`output/peoplegroups.json`** - Centrally managed groups
  - Military units, countries, organizations
  - Appends event mentions to existing groups
  
- **`output/supplemental.json`** - All supplemental materials
  - Endnotes, footnotes, bibliography
  - Tracks online/offline resources

**Purpose:** Central management per requirements

**Behavior:**
- Load existing data
- Append new mentions
- Deduplicate by name/ID
- Save back to file

## Cache Management

### Clear All Caches
```bash
rm -rf cache/
```

### Clear Specific API Cache
```python
grok_client.clear_cache('events')  # Clear only event cache
```

### Clear All API Caches
```python
grok_client.clear_cache()  # Clear all API caches
```

### Clear Downloaded Content
```bash
rm -rf cache/images/
rm -rf cache/maps/
```

## Cache Benefits

1. **Cost Savings** - Avoid duplicate API calls
2. **Speed** - Instant results from cache
3. **Reliability** - Works offline after first run
4. **Debugging** - Can inspect cached responses
5. **Separation** - Different types don't interfere

## Cache Structure Example

```
cache/
├── api/                    # Grok API response cache
│   ├── events/
│   │   ├── abc123.db      # Cached event extractions
│   │   └── ...
│   ├── dates/
│   ├── places/
│   ├── people/
│   ├── peoplegroups/
│   ├── weather/
│   └── supplemental/
├── images/                 # Downloaded images
│   ├── image1.jpg
│   ├── image1.json        # Metadata
│   └── ...
└── maps/                   # Downloaded maps
    ├── map1.jpg
    ├── map1.json          # Metadata
    └── ...

output/
├── people.json            # Central people data
├── peoplegroups.json      # Central groups data
└── supplemental.json      # Central supplemental data
```
