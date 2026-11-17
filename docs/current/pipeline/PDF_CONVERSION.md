# PDF to Markdown Conversion

**Last Updated:** 2026-11-16

## Status

✅ **Supported for local, text-based PDFs** — `scripts/pdf_to_markdown.py` (pymupdf4llm).

> **In AWS, PDFs are not converted this way.** A PDF uploaded to `contentrepository/` goes through Phase 0: the trigger Lambda scans it, splits it into page chunks and runs **Chandra OCR** on AWS Batch GPU; the OCR merge Lambda then sanitises, translates to English, splits into chapters, and publishes `contentrepository/<slug>/chapterN/chapterN-{meta.yaml,content.md}` (`lambda_handlers/ocr_merge_handler.py` -> `src/ingestion/ocr_promote.promote`). That path also handles scans and keeps the book's own figures (Phase 1 builds them from the PDF). See [OCR_OPERATIONS.md](../OCR_OPERATIONS.md) and [PIPELINE.md](../core/PIPELINE.md). Use this script only for a quick local conversion of a text PDF.

## Usage

```bash
# Install dependency
pip install pymupdf4llm

# Convert PDF
python3 scripts/pdf_to_markdown.py <pdf_file> <BookName>

# Example
python3 scripts/pdf_to_markdown.py paper.pdf "SmithPaper2024"
```

## What It Does

The script uses **pymupdf4llm** (not raw PyMuPDF/fitz) to convert PDFs to structured markdown with layout preservation. It:

1. Extracts markdown from the PDF via `pymupdf4llm.to_markdown()`
2. Creates `contentrepository/<BookName>/chapter1/` with:
   - `chapter1-content.md` — extracted markdown
   - `chapter1-meta.yaml` — metadata template (fill in manually)
3. Creates `contentrepository/<BookName>/sourcedocument/` and **moves the original PDF** there (Phase 1 discovery skips `sourcedocument/`)

`<BookName>` becomes the book slug and cannot be changed after ingestion (`src/ingestion/book_slug.py`).

## After Conversion

```bash
# 1. Fill in metadata
vim contentrepository/BookName/chapter1/chapter1-meta.yaml

# 2. Review/clean content
vim contentrepository/BookName/chapter1/chapter1-content.md

# 3. Split into chapters if needed

# 4. Run pipeline
python3 phase1_parse.py
python3 phase2_retry.py
```

## Quality Checklist

After conversion, review the content for:

- [ ] Metadata fields filled in
- [ ] Paragraph breaks correct
- [ ] Headers/footers removed
- [ ] Footnotes formatted: `[^1]: text`
- [ ] Tables readable
- [ ] Special characters correct (é, ö, etc.)
- [ ] No extraction artifacts

## Handling Scanned PDFs

pymupdf4llm works with text-based PDFs. For scanned documents, upload the PDF to `contentrepository/` and let Phase 0 run Chandra OCR (see above). For a purely local run you can OCR first:

```bash
pip install ocrmypdf
ocrmypdf input.pdf output.pdf
python3 scripts/pdf_to_markdown.py output.pdf "BookName"
```

## Related

- [Adding Data Sources](ADDING_DATA_SOURCES.md)
- [Papers and Articles](PAPERS_AND_ARTICLES.md)
- [HyperWar HTML Import](HYPERWAR_HTML_IMPORT.md)
