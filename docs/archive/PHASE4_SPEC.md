# Phase 4: Document Acquisition & Processing

**Status:** Design  
**Date:** 2026-05-09

---

## Purpose

Download digitized primary source documents discovered during Phase 3 enrichment, extract their text content, and feed them back through Phases 1-3 as new source material. This creates a recursive enrichment loop where bibliography references become extractable documents.

---

## Input

Bibliography entries with `search_status: "resolved"` and non-empty `resource_urls` pointing to downloadable documents (PDFs, images, HTML pages).

---

## Pipeline Flow

```
Phase 3 output (bibliography with URLs)
    ↓
Phase 4a: Triage — classify URLs by type, filter downloadable
    ↓
Phase 4b: Download — fetch documents to S3
    ↓
Phase 4c: Extract text — OCR/PDF extraction → markdown
    ↓
Phase 4d: Feed — place markdown in contentrepository/, trigger Phase 1
```

---

## Phase 4a: Triage

Classify each `resource_url` and determine if it's processable:

| URL Type | Action | Example |
|----------|--------|---------|
| PDF (direct link) | Download | `archive.org/download/...pdf` |
| PDF (viewer page) | Extract direct link, download | Document viewer pages |
| Image (TIFF/JPEG) | Download, OCR | `s3.amazonaws.com/NARAprodstorage/...tif` |
| HTML page | Extract text content | Blog posts, association pages |
| Subscription-gated | Skip, mark `download_status: "gated"` | Paywalled content |
| Video/Audio | Skip | YouTube, oral histories |

**Output fields on bibliography entry:**
```json
{
  "download_status": "pending|downloaded|extracted|gated|skipped|error",
  "download_path": "s3://bucket/downloads/RG407_Entry427_1stDiv_AAR_Jul44.pdf",
  "extracted_text_path": "contentrepository/acquired/1stDiv_AAR_Jul44.md",
  "download_date": "2026-05-09",
  "document_type": "pdf|image|html",
  "page_count": 12
}
```

---

## Phase 4b: Download

**Storage:** `s3://dev-wwii-data-pipeline/downloads/{book}/{filename}`

**Dedup:** Hash the URL. If already downloaded (check DynamoDB `download#{url_hash}`), skip.

**Rate limiting:**
- Archive.org: 1 req/sec (robots.txt compliant)
- HathiTrust: public domain volumes only
- NARA S3: no limit (public bucket)
- General web: 2 req/sec with polite User-Agent

**Size limits:**
- Max file size: 100MB per document
- Max pages per document: 500 (skip massive compilations)
- Max total downloads per run: configurable (default 50)

---

## Phase 4c: Text Extraction

| Source Format | Tool | Output |
|---------------|------|--------|
| Digital PDF | `pdfplumber` or `PyMuPDF` | Markdown with page breaks |
| Scanned PDF | Tesseract OCR via `pdf2image` + `pytesseract` | Markdown (noisy) |
| TIFF/JPEG images | Tesseract OCR | Markdown |
| HTML pages | `BeautifulSoup` text extraction | Markdown |

**OCR quality handling:**
- Confidence score per page (Tesseract provides this)
- Pages below 60% confidence flagged for manual review
- Common OCR corrections for military abbreviations (e.g., "l" → "1" in unit numbers)

**Output format:** Markdown file placed in `contentrepository/acquired/{source_book}/{document_name}.md`

**Metadata header:**
```markdown
---
title: 1st Infantry Division After Action Report, July 1944
source: NARA RG 407, Entry 427
acquired_from: https://archive.org/download/...
acquisition_date: 2026-05-09
ocr_confidence: 0.82
pages: 12
parent_bibliography_id: 01KPVDNESNT8FBN8SW250413ZZ
---
```

---

## Phase 4d: Feed Back

Place extracted markdown in `contentrepository/acquired/` and trigger Phase 1. The S3 notification on `contentrepository/` prefix fires the pipeline automatically.

**Depth control:**
- `acquisition_depth: 1` — documents acquired from original books
- `acquisition_depth: 2` — documents referenced by depth-1 documents
- **Max depth: 2** — prevents infinite recursion
- Configurable: `phase4.max_depth: 2` in config.yaml

**Lineage tracking:**
```json
{
  "acquired_from_bibliography": "01KPVDNESNT8FBN8SW250413ZZ",
  "acquisition_depth": 1,
  "source_book": "BreakoutAndPursuit"
}
```

---

## Scope Control

**Per-run limits (config.yaml):**
```yaml
phase4:
  enabled: true
  max_downloads_per_run: 50
  max_depth: 2
  max_file_size_mb: 100
  max_pages_per_document: 500
  ocr_min_confidence: 0.60
  rate_limit_per_second: 2
  skip_gated: true
  allowed_domains: []  # empty = all domains allowed
  blocked_domains:
    - youtube.com
    - facebook.com
    - twitter.com
```

**Priority order for downloads:**
1. NARA digitized records (highest value — primary sources)
2. Archive.org public domain documents
3. HathiTrust public domain volumes
4. University/museum hosted documents
5. General web sources (lowest priority)

---

## DynamoDB Keys

- `download#{url_hash}` — tracks download status, prevents re-download
- `lock#dev-wwii-phase4-acquire` — pipeline lock

---

## ECS Task

Same container as Phases 1-3. New entrypoint script: `phase4_acquire.py`

**Dependencies (new):**
- `pytesseract` + Tesseract binary (add to Dockerfile)
- `pdf2image` + `poppler-utils` (add to Dockerfile)
- `pdfplumber` or `PyMuPDF`
- `beautifulsoup4` (already present)

---

## Error Handling

| Error | Action |
|-------|--------|
| 403/401 (gated) | Mark `download_status: "gated"`, skip |
| 404 (dead link) | Mark `download_status: "error"`, cache negative |
| Timeout | Retry once, then mark error |
| OCR failure | Mark `download_status: "ocr_failed"`, keep raw file |
| Corrupt PDF | Mark error, skip |

---

## Cost Estimate

- **Storage:** ~10MB average per document × 50 docs/run = 500MB/run (~$0.01/month S3)
- **Compute:** OCR is CPU-intensive. 512 CPU / 1GB memory may be insufficient for large PDFs. Consider 1024 CPU / 2GB for Phase 4 task.
- **Tesseract:** Free, open source
- **Grok:** No additional calls (validation already done in Phase 3)

---

## Implementation Order

1. `phase4_acquire.py` — download logic with triage and rate limiting
2. `src/extraction/document_ocr.py` — text extraction (PDF + image + HTML)
3. Dockerfile update — add Tesseract, poppler-utils
4. CloudFormation — Phase 4 task definition
5. Config — `phase4` section in config.yaml
6. Integration — S3 notification triggers Phase 1 on new `contentrepository/acquired/` files
