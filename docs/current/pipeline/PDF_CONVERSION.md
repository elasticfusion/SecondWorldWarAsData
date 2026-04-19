# PDF to Markdown Conversion

## Status

✅ **Supported** — `scripts/pdf_to_markdown.py`

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
3. Creates `contentrepository/<BookName>/sourcedocument/` and **moves the original PDF** there

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

pymupdf4llm works with text-based PDFs. For scanned documents, run OCR first:

```bash
pip install ocrmypdf
ocrmypdf input.pdf output.pdf
python3 scripts/pdf_to_markdown.py output.pdf "BookName"
```

## Related

- [Adding Data Sources](ADDING_DATA_SOURCES.md)
- [Papers and Articles](PAPERS_AND_ARTICLES.md)
- [HyperWar HTML Import](HYPERWAR_HTML_IMPORT.md)
