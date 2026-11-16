# Maps Extraction User Guide

Extract maps and diagrams from WWII source material.

## Overview

Maps (and diagrams) from the source books are **found in Phase 1** and **persisted as Map entities in Phase 3**. Phase 2 has no map step. (The old per-event extractor `src/extraction/maps.py` is no longer called by any pipeline entry point.)

Where a document's maps come from (Phase 1, written into `output/content/<book>/chapterN<section>-parsed.json` `maps[]`):
- **Web (HyperWar) sources:** links in the text (`[Map VIII](maps/...jpg)`), found by `parser.extract_maps` and resolved to absolute URLs.
- **OCR'd PDF books:** the book's own figures, built once in Phase 1 by `src/ingestion/book_figures` (+ `pdf_figures`) from the PDF and the OCR crops, into `contentrepository/<book>/_figures/figures.json` + `files/`. Each numbered map goes to **every chapter that prints or cites it**, with the citing paragraphs as its reference footprint. A figure's `url` is its key under `_figures/files/`; image bytes are read from storage (`images.fetch_image_bytes`), not the web.

Phase 3 (`phase3_enrich_data.py`, step `in_doc_map_links`): `map_link_source.persist_map_entities` writes one Map entity per map **keyed by url**, accumulating each chapter's reference footprint in `references[]` (a roman-numeral fold-out shared by many chapters or books is one record). It downloads the image (`map_common.download_map_image`), classifies the map type, and links Place / Date / Organization from the footprint's sub-events (`map_common.link_map_entities`). Map entities are persisted regardless of `external_maps` flags. Separately, when `external_maps.recycle_in_doc_maps` is true, each in-document map is also recycled as a map-as-source document (one paid vision call each).

## Quick Start

### Maps config

Edit `config.yaml` (current values):

```yaml
maps:
  enabled: true
  extract_during_phase1: true
  download_images: true         # map bytes are kept in the corpus
  storage_backend: "filesystem"
  storage_path: "output/maps/"
  image_storage_path: "output/maps/files/"
```

Maps are written by Phase 3:

```bash
python3 phase3_enrich_data.py
```

## Storage

Maps are stored on the filesystem under `output/maps/` (synced to S3 with the rest of `output/`). The `s3` storage backend described in earlier versions of this guide was never implemented.

**Output Structure:**
```
output/maps/
├── {MapID}.json              # Map metadata (+ references[])
├── index.json                # url -> map file index (accumulation survives across runs)
└── files/
    ├── {MapID}.jpg           # Downloaded images
    └── {MapID}.png
```

## Map Metadata

Each map has a JSON file with comprehensive metadata and **automatic entity linking**:

```json
{
  "MapID": "01KJ8X9Y2Z3A4B5C6D7E8F9G0H",
  "map_title": "German Invasion Routes - September 1, 1939",
  "figure_number": "Map 3",
  "references": [
    { "book": "Poland", "chapter": "chapter1", "paragraph_number": 12, "Sub_eventID": "01KJ8X2Y..." }
  ],
  "EventID": "01KJ8X1Y...",
  "Event_Name": "Invasion of Poland",
  "Sub_eventID": "01KJ8X2Y...",
  "Sub_event_Name": "German forces cross the Polish border",
  "place_name": "Poland",
  "PlaceMentionID": "01KJ8X3Y...",
  "date": "1939-09-01",
  "DateMentionID": "01KJ8X4Y...",
  "local_image_path": "output/maps/files/01KJ8X9Y.jpg",
  "source_url": "https://...",
  "file_format": "jpg",
  "extracted_date": "2026-02-24T13:19:00Z",
  "description": "Map showing three main German invasion routes",
  "map_type": null,
  "storage_backend": "filesystem"
}
```

`references[]` is the map's **reference footprint** (every chapter/paragraph printing or citing it, deduped, each stamped with its `book`). Exact fields come from `map_common.base_map_record` and the enforced `maps` schema in `src/schemas/`.

**Automatic Linking** (`map_common.link_map_entities`, from the footprint's Sub_eventIDs; only fills an empty link):
- PlaceMentionID/place_name: matched via Sub_eventID in the places repository
- DateMentionID/date: matched via Sub_eventID in the dates repository
- PeopleGroupID/PeopleGroup_Name: matched via Sub_eventID in the people_groups repository

## Image Download

### Enable Image Download

```yaml
maps:
  download_images: true
  download_timeout: 30
```

Downloads go through the SSRF-safe `images.fetch_image_bytes` gate (an image/* body, byte-capped, no redirects; a book figure key is read from storage). The extension comes from the content type, else the URL suffix, default `jpg`. A blocked or failed download leaves `local_image_path` null and logs `OBSERV maps.download_failed` / `maps.download_blocked`.

### Without Image Download

If `download_images: false`:
- Only metadata is extracted
- `local_image_path` will be null
- `source_url` preserved for manual download

## Linking to Other Entities

Maps are **automatically linked** to places, dates and organizations from the sub-events of their reference footprint.

### Events
- Each `references[]` entry carries the paragraph (and `Sub_eventID` when located) where the map is printed or cited. A map with no located reference is a loud provenance-gap warning in Phase 1.

### Places (Automatic ✅)
- `PlaceMentionID` - Place linked to the sub-event
- `place_name` - Name of the place
- Matched by searching places repository for Sub_eventID
- Example: Map in Normandy sub-event → links to Normandy place

### Dates (Automatic ✅)
- `DateMentionID` - Date linked to the sub-event
- `date` - ISO date string
- Matched by searching dates repository for Sub_eventID
- Example: Map in June 6, 1944 sub-event → links to that date

**No manual configuration required** - linking happens automatically in Phase 3.

## Querying Maps

### Find All Maps

```bash
ls output/maps/*.json | wc -l  # includes index.json
```

### Find Maps by Book

```bash
jq -r 'select(any(.references[]?; .book == "USA-E-XChannel")) | .map_title' output/maps/*.json
```

### Find Maps by Date

```bash
jq -r 'select(.date == "1944-06-06") | .map_title' output/maps/*.json
```

### Find Maps by Place

```bash
jq -r 'select(.place_name == "Normandy") | .map_title' output/maps/*.json
```

## Troubleshooting

### No Maps Found

**Cause:** No maps in Phase 1 parsed files

**Solution:**
1. Check Phase 1 output: `jq '.maps' output/content/<book>/chapter*-parsed.json`
2. Web source: verify the markdown links maps (`parser.extract_maps`)
3. OCR'd PDF book: check `contentrepository/<book>/_figures/figures.json` (built by `book_figures.ensure`)

### Download Failures

**Cause:** Network issues or invalid URLs

**Solution:**
1. Check logs for specific errors
2. Increase timeout: `download_timeout: 60`
3. Verify URLs are accessible
4. Check firewall/proxy settings

## Best Practices

1. Keep `download_images: true` so the corpus holds the bytes (the source host can vanish).
2. Keep map storage under `output/` (only `output/` and `artifacts/` are synced to S3; `filestore/` is ephemeral).
3. Map entities are keyed by url: do not re-key or hand-edit `index.json`.

## Related Documentation

- Archived: `docs/archive/2026-11-16/features/maps/S3_STORAGE.md` (the never-implemented S3 backend)
- `docs/archive/contextmanagement/Specs/maps.md` - Technical specification (historical; current schema: `src/schemas/`)
- `docs/archive/contextmanagement/Specs/maps_v1_schema.json` - JSON schema (historical; current schema: `src/schemas/`)
- [PIPELINE.md](../../core/PIPELINE.md) - Complete pipeline documentation

## Map-Interior Extraction (new)

This guide covers map **cataloging** (metadata, download, entity linking). Extracting the
**interior** of an operational map — the places, units (with affiliation/echelon), dated
movements, and fortifications drawn on it — into structured, entity-linked data is a
separate capability. See **[MAP_FEATURES_SCHEMA.md](MAP_FEATURES_SCHEMA.md)** for the
enforced GeoJSON `map_features` schema, the Grok-vision prototype
(`scripts/proto_map_vision.py`), unit resolution via the shared `unit_key` with APP-6
symbology constraints, and the reverse-registration pass
(`src/extraction/map_registration.py`) that links narrative entities onto a map by
`(PlaceID, DateID)` coverage.

## Future Enhancements

- [x] Auto-link maps to EventID/Sub_eventID
- [x] Auto-link maps to PlaceMentionID
- [x] Auto-link maps to DateMentionID
- [x] OCR text extraction from map images — via the Chandra OCR pass (prose/labels; see
  `docs/current/dataquality/CHANDRA_OCR_DESIGN.md`)
- [x] Map image analysis (boundaries, features) — **prototype**: map-interior extraction
  (Tier-1 inventory + Tier-2 dated movement); see MAP_FEATURES_SCHEMA.md
- [x] Page numbers: OCR'd PDF figures carry their PDF page and printed page (`figures.json`)
- [ ] Wire the map-interior prototype into the pipeline + enforced output records
- [ ] Map type classification refinement (tactical / strategic / political)

## Phase 3 Enrichment

**None.** Map cataloging (this guide) and the map-interior prototype are not wired into
Phase 3. When map-interior extraction productionizes, a gated Phase-3 map step (extraction →
entity resolution → reverse registration) would run. See
[Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md) (Known Gaps).
