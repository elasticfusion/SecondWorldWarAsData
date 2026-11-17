# Content Contributor Guide

A guide for adding WWII historical content to the extraction pipeline. No coding required.

**Last Updated:** 2026-11-16

---

## Overview

You provide WWII source documents (books, chapters) in markdown format. The pipeline automatically extracts people, places, military units, dates, events, and other structured data using AI.

Your role:
1. **Prepare content** — format source material as markdown
2. **Upload** — push to S3 (AWS) or local directory
3. **Review duplicates** — merge or reclassify entities in the web UI
4. **Done** — enriched data appears in DynamoDB/JSON

---

## Quick Start

### Option A: Import from HyperWar (easiest)

If the book is on [ibiblio.org/hyperwar](https://www.ibiblio.org/hyperwar/):

```bash
python3 scripts/import_hyperwar_html.py https://www.ibiblio.org/hyperwar/USA/USA-E-Example/index.html
```

The script prompts for book metadata, downloads all chapters, and converts to markdown.

### Option B: Manual preparation

Create this directory structure:

```
contentrepository/
└── YourBookName/
    ├── chapter1/
    │   ├── chapter1-meta.yaml
    │   └── chapter1a-content.md
    ├── chapter2/
    │   ├── chapter2-meta.yaml
    │   ├── chapter2a-content.md
    │   └── chapter2b-content.md
    └── ...
```

### Option C: Upload the PDF, EPUB or DOCX itself (AWS)

Upload the file to `contentrepository/` (see Upload and Process). The pipeline routes it by type:
a PDF or scan is OCR'd on a GPU (Chandra), translated to English if needed, split at its own
chapter headings and published as `contentrepository/<name>/chapterN/chapterN-{meta.yaml,content.md}`;
EPUB/DOCX/HTML are converted with pandoc. The book name (its **slug**) is the file name without
its extension and never changes: re-uploading the same document under another name is rejected.
Book metadata is found for you and recorded with evidence quotes in the chapter meta. Uploads the
pipeline cannot use (no usable name, wrong file type, virus scan hit) go to `needs-review/`.
To attach a transcription for an automatic cross-check of the OCR, upload with
`--metadata validation-url=<url>` (see [OCR_OPERATIONS.md](OCR_OPERATIONS.md)).

For a local PDF-to-markdown conversion, `python3 scripts/pdf_to_markdown.py path/to/book.pdf` still
exists (see [PDF Conversion](pipeline/PDF_CONVERSION.md)).

---

## Content Format

### Metadata (required per chapter)

Create `chapter*-meta.yaml`:

```yaml
series: "United States Army in World War II"
book: "Your Book Title"
author: "Author Name"
chapter_number: 1
chapter_title: "Chapter Title"
license: "Public Domain"
copyright_date: "1950"
source_url: "https://..."
```

**Required fields:** `series`, `book`, `author`, `license`
**Auto-completed:** `chapter_number`, `chapter_title` (run `python3 scripts/complete_metadata_with_grok.py`)

### Content (markdown)

Write `chapter*-content.md`:

```markdown
The first paragraph of the chapter.

The second paragraph continues the narrative.
```

**Supported elements:** paragraphs (blank-line separated), notes, images, maps and page markers. The exact syntax and a complete example chapter are in [pipeline/MARKDOWN_CONTENT_FORMAT.md](pipeline/MARKDOWN_CONTENT_FORMAT.md).

### Sub-chapters

Long chapters should be split into sections (a, b, c, etc.):

```
chapter3/
├── chapter3-meta.yaml      # one metadata file
├── chapter3a-content.md    # first section
├── chapter3b-content.md    # second section
└── chapter3c-content.md    # third section
```

The HyperWar import script does this automatically at `###` headings.

---

## Upload and Process

### AWS Mode

Upload to S3 to trigger the pipeline automatically:

```bash
aws s3 sync contentrepository/ s3://dev-wwii-data-pipeline/contentrepository/ --region us-east-1
# or one source file:
aws s3 cp "My Book.pdf" s3://dev-wwii-data-pipeline/contentrepository/ --region us-east-1
```

The pipeline runs: **Ingest (OCR/convert) → Parse → Extract → Dedup Review → Enrich → Import**

You'll receive an email when Phase 2 completes and it's time to review duplicates.

### Local Mode

```bash
python3 phase1_parse.py          # parse markdown → JSON
python3 phase2_extract.py        # extract entities via Grok API
python3 phase3_enrich_data.py    # enrich: geocoding, NOAA, OpenSERP, archives
```

---

## Review Duplicates

After extraction, the pipeline detects potential duplicate entities. In AWS mode, a web UI is provided.

### Accessing the Review UI

Open the Dedup Review URL (provided in the completion email or stack outputs). Log in with the credentials set during deployment.

### Actions

For each duplicate group:

| Action | What it does |
|--------|-------------|
| **Merge Selected** | Combine checked entries into the selected primary |
| **Skip** | Leave as-is, remove from review |
| **Not Duplicates** | Add to exclusion list |
| **↗ Move to...** | Reclassify an entity (e.g., move a military unit from "places" to "groups") |

### Tips

- **View Details** (▶ button) shows the entity JSON including event mentions
- **Uncheck** entries to exclude them from a merge — they'll be re-grouped for separate review
- **Primary** radio selects which record survives the merge
- Military units appearing as people or places can be reclassified using the dropdown

### Mark Complete

When finished reviewing, click **"Mark Review Complete & Start Phase 3"**. This unblocks enrichment.

You don't need to review every group — just the obvious ones. You can always re-run dedup later.

---

## What Gets Extracted

From your content, the pipeline extracts:

| Entity | Description | Example |
|--------|-------------|---------|
| Events | Battles, operations with sub-events | Operation Overlord → D-Day landings |
| Dates | Temporal mentions with precision | "early June 1944" |
| Places | Locations with GPS coordinates | Normandy (49.18, -0.37) |
| People | Biographical profiles | Eisenhower — Supreme Commander |
| Military Units | Organizations and hierarchies | 1st Infantry Division |
| Equipment | Weapons, vehicles, specifications | M4 Sherman tank |
| Weather | Historical weather data | Overcast, wind 15mph |
| Logistics | Supply chain information | Ammunition shortage |
| Casualties | Casualty tracking | 2,500 killed in action |
| Maps | Source and external maps | Normandy invasion map |
| Citations | Bibliography references | Harrison, Cross-Channel Attack |

---

## Monitoring Progress

### AWS Mode

```bash
# Pipeline logs
aws logs tail /ecs/dev-wwii-pipeline --follow --region us-east-1

# Check running tasks
aws ecs list-tasks --cluster dev-wwii-pipeline --region us-east-1

# Metrics dashboard
# Open: https://<metrics-api-id>.execute-api.us-east-1.amazonaws.com/prod/metrics
```

### Local Mode

Watch the terminal output. Each chapter logs extraction progress.

---

## Troubleshooting

### No chapters found after upload
- Verify directory structure: `contentrepository/BookName/chapter1/chapter1-meta.yaml`
- Check that metadata YAML is valid: `python3 -c "import yaml; yaml.safe_load(open('chapter1-meta.yaml'))"`

### Missing metadata fields
```bash
python3 scripts/generate_missing_metadata.py
python3 scripts/complete_metadata_with_grok.py
```

### Entities in wrong category
Use the **Reclassify** feature in the Dedup Review UI (↗ button) to move entities between people, places, and groups.

### Pipeline not triggering (AWS)
```bash
# Check S3 notifications
aws s3api get-bucket-notification-configuration --bucket dev-wwii-data-pipeline --region us-east-1

# Check trigger Lambda
aws logs tail /aws/lambda/dev-wwii-trigger --region us-east-1 --since 5m
```

---

## Related Documentation

- [Adding Data Sources](pipeline/ADDING_DATA_SOURCES.md) — detailed technical guide
- [HyperWar HTML Import](pipeline/HYPERWAR_HTML_IMPORT.md) — import from HyperWar
- [PDF Conversion](pipeline/PDF_CONVERSION.md) — convert PDFs to markdown
- [Schema Reference](SCHEMA_REFERENCE.md) — JSON output format
- [Pipeline Overview](core/PIPELINE.md) — technical pipeline details
