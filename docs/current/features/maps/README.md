# Maps Extraction User Guide

Extract maps and diagrams from WWII source material.

## Overview

Maps extraction identifies and processes maps, diagrams, and illustrations from the source books. Maps are discovered during Phase 1 parsing and extracted in Phase 2.

## Quick Start

### Enable Maps Extraction

Edit `config.yaml`:

```yaml
maps:
  enabled: true
  download_images: false        # Set true to download images
  storage_backend: "filesystem"
```

Run extraction:

```bash
python3 phase2_extract.py
```

## Configuration Options

### Basic Configuration

```yaml
maps:
  enabled: true                   # Enable/disable maps extraction
  extract_during_phase1: true     # Extract during document parsing
  download_images: false          # Download actual image files
  storage_backend: "filesystem"   # filesystem or s3
```

### Filesystem Storage (Default)

```yaml
maps:
  storage_backend: "filesystem"
  storage_path: "output/maps/"
  image_storage_path: "output/maps_images/"
  download_timeout: 30
```

**Output Structure:**
```
output/maps/
├── {MapID}.json              # Map metadata
├── {MapID}.json
├── index.json                # Map index
output/maps_images/
├── {MapID}.jpg               # Downloaded images
├── {MapID}.png
```

### S3 Storage

```yaml
maps:
  storage_backend: "s3"
  s3_bucket: "your-bucket-name"
  s3_prefix: "maps/"
  s3_region: "us-east-1"
  download_images: true
```

**S3 Structure:**
```
s3://your-bucket/maps/
├── metadata/{MapID}.json
├── images/{MapID}.jpg
```

See [S3_STORAGE.md](S3_STORAGE.md) for detailed S3 setup.

## Map Metadata

Each map has a JSON file with comprehensive metadata and **automatic entity linking**:

```json
{
  "MapID": "01KJ8X9Y2Z3A4B5C6D7E8F9G0H",
  "map_title": "German Invasion Routes - September 1, 1939",
  "source_book": "The German Campaign in Poland (1939)",
  "source_author": "Robert M. Kennedy",
  "source_series": "United States Army in World War II",
  "page_number": null,
  "figure_number": "Map 3",
  "EventID": "01KJ8X1Y...",
  "Event_Name": "Invasion of Poland",
  "Sub_eventID": "01KJ8X2Y...",
  "Sub_event_Name": "German forces cross the Polish border",
  "place_name": "Poland",
  "PlaceMentionID": "01KJ8X3Y...",
  "date": "1939-09-01",
  "DateMentionID": "01KJ8X4Y...",
  "local_path": "output/maps/01KJ8X9Y.json",
  "local_image_path": "output/maps_images/01KJ8X9Y.jpg",
  "source_url": "https://...",
  "file_format": "jpg",
  "extracted_date": "2026-02-24T13:19:00Z",
  "description": "Map showing three main German invasion routes",
  "map_type": null,
  "storage_backend": "filesystem"
}
```

**Automatic Linking:**
- EventID/Sub_eventID: From event file structure
- PlaceMentionID/place_name: Matched via Sub_eventID in places repository
- DateMentionID/date: Matched via Sub_eventID in dates repository

## Image Download

### Enable Image Download

```yaml
maps:
  download_images: true
  download_timeout: 30
```

**Supported Formats:**
- JPG/JPEG
- PNG
- TIFF/TIF
- PDF

**Content-Type Detection:**
- Automatically detects format from HTTP headers
- Falls back to URL extension
- Defaults to JPG if unknown

### Without Image Download

If `download_images: false`:
- Only metadata is extracted
- `local_image_path` will be null
- `source_url` preserved for manual download

## Linking to Other Entities

Maps are **automatically linked** to events, places, and dates based on the sub-event context where they appear.

### Events (Automatic ✅)
- `EventID` - Parent event containing the map
- `Sub_eventID` - Specific sub-event where map appears
- Extracted from event file structure

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

**No manual configuration required** - all linking happens automatically during extraction.

## Querying Maps

### Find All Maps

```bash
ls output/maps/*.json | wc -l
```

### Find Maps by Book

```bash
jq -r 'select(.source_book == "Cross-Channel Attack") | .map_title' output/maps/*.json
```

### Find Maps by Date

```bash
jq -r 'select(.date == "1944-06-06") | .map_title' output/maps/*.json
```

### Find Maps by Place

```bash
jq -r 'select(.place_name == "Normandy") | .map_title' output/maps/*.json
```

## Storage Backend Comparison

| Feature | Filesystem | S3 |
|---------|-----------|-----|
| **Setup** | None | AWS credentials |
| **Cost** | Free | ~$0.023/GB/month |
| **Access** | Local only | Global |
| **Backup** | Manual | Automatic (versioning) |
| **Scalability** | Limited | Unlimited |
| **Speed** | Fast | Network dependent |

## Troubleshooting

### No Maps Found

**Cause:** No maps in Phase 1 parsed files

**Solution:**
1. Check Phase 1 output: `grep -r "maps" output/*/chapter*-parsed.json`
2. Verify source markdown has map references
3. Check map extraction patterns in `src/parser.py`

### Download Failures

**Cause:** Network issues or invalid URLs

**Solution:**
1. Check logs for specific errors
2. Increase timeout: `download_timeout: 60`
3. Verify URLs are accessible
4. Check firewall/proxy settings

### S3 Upload Failures

**Cause:** AWS credentials or permissions

**Solution:**
1. Verify credentials: `aws s3 ls`
2. Check bucket exists: `aws s3 ls s3://your-bucket/`
3. Review IAM permissions
4. See [S3_STORAGE.md](S3_STORAGE.md) for details

## Best Practices

1. **Start without image download** to test metadata extraction
2. **Enable download** only for maps you need
3. **Use S3** for production/shared environments
4. **Use filesystem** for development/testing
5. **Monitor storage costs** when using S3
6. **Backup local files** regularly

## Related Documentation

- [S3_STORAGE.md](S3_STORAGE.md) - S3 configuration guide
- `contextmanagement/Specs/maps.md` - Technical specification
- `contextmanagement/Specs/maps_v1_schema.json` - JSON schema
- [PIPELINE.md](PIPELINE.md) - Complete pipeline documentation

## Examples

### Extract Maps (Metadata Only)

```yaml
maps:
  enabled: true
  download_images: false
```

```bash
python3 phase2_extract.py
```

### Extract Maps with Images (Filesystem)

```yaml
maps:
  enabled: true
  download_images: true
  storage_backend: "filesystem"
```

```bash
python3 phase2_extract.py
```

### Extract Maps to S3

```yaml
maps:
  enabled: true
  download_images: true
  storage_backend: "s3"
  s3_bucket: "wwii-maps"
  s3_region: "us-east-1"
```

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
python3 phase2_extract.py
```

## Future Enhancements

- [ ] Auto-link maps to EventID/Sub_eventID ✅ **DONE**
- [ ] Auto-link maps to PlaceMentionID ✅ **DONE**
- [ ] Auto-link maps to DateMentionID ✅ **DONE**
- [ ] Extract page numbers from parsed documents
- [ ] OCR text extraction from map images
- [ ] Map image analysis (boundaries, features)
- [ ] Map type classification (tactical, strategic, political)
