# URL Content Extraction (Optional)

## Overview

Extract content from web pages and convert to the chapter/section structure used by the pipeline.

## Features

- Fetches HTML from URL
- Extracts main content (removes nav, footer, etc.)
- Converts HTML to markdown
- Splits into chapters based on heading patterns
- Saves in `contentrepository/` structure
- Ready for Phase 1 parsing

## Installation

```bash
pip install beautifulsoup4 html2text
```

## Usage

### Basic Extraction

```bash
python3 extract_url.py \
  --url "https://example.com/book.html" \
  --book-name "MyBook"
```

### With Content Selector

If the page has a specific content container:

```bash
python3 extract_url.py \
  --url "https://example.com/book.html" \
  --book-name "MyBook" \
  --content-selector "article.main-content"
```

### Custom Chapter Pattern

If chapters use a different heading format:

```bash
python3 extract_url.py \
  --url "https://example.com/book.html" \
  --book-name "MyBook" \
  --chapter-pattern "^#{1,2}\s+Part\s+(\d+)"
```

## Output Structure

Creates the same structure as manual markdown files:

```
contentrepository/
└── MyBook/
    ├── chapter1/
    │   ├── chapter1-meta.md
    │   └── chapter1-content.md
    ├── chapter2/
    │   ├── chapter2-meta.md
    │   └── chapter2-content.md
    └── ...
```

## Options

- `--url` - Source URL (required)
- `--book-name` - Name for book directory (required)
- `--output-dir` - Output directory (default: contentrepository)
- `--content-selector` - CSS selector for main content (optional)
- `--chapter-pattern` - Regex for chapter headings (optional)

## Default Behavior

### Content Extraction
Tries these selectors in order:
1. Custom selector (if provided)
2. `article`
3. `main`
4. `[role="main"]`
5. `.content`
6. `#content`
7. `body` (fallback)

Removes: `script`, `style`, `nav`, `footer`, `header`

### Chapter Detection
Default pattern: `^#{1,2}\s+Chapter\s+(\d+|[IVXLCDM]+)`

Matches:
- `# Chapter 1`
- `## Chapter I`
- `# CHAPTER 5`

### Metadata
Creates meta files with:
- Source URL
- Book name
- Chapter title
- License note (to check source)

## Examples

### Example 1: Simple Book
```bash
python3 extract_url.py \
  --url "https://www.gutenberg.org/files/12345/12345-h/12345-h.htm" \
  --book-name "ClassicNovel"
```

### Example 2: With Specific Content Area
```bash
python3 extract_url.py \
  --url "https://example.com/history/wwii.html" \
  --book-name "WWIIHistory" \
  --content-selector "#main-content"
```

### Example 3: Custom Chapter Pattern
```bash
python3 extract_url.py \
  --url "https://example.com/book.html" \
  --book-name "TechnicalManual" \
  --chapter-pattern "^#{1,2}\s+Section\s+(\d+)"
```

## After Extraction

1. **Review extracted files** - Check formatting and content
2. **Edit if needed** - Fix any conversion issues
3. **Run Phase 1** - Parse the extracted content:
   ```bash
   python3 phase1_parse.py
   ```

## Limitations

- Requires well-structured HTML
- Chapter detection depends on consistent heading format
- May need manual cleanup for complex layouts
- Respects robots.txt and rate limits
- Check source license before processing

## Troubleshooting

### No chapters found
- Check the chapter heading format in source
- Adjust `--chapter-pattern` to match
- Content saved as single chapter by default

### Missing content
- Try different `--content-selector`
- Check if content is dynamically loaded (JavaScript)
- May need to save HTML manually first

### Formatting issues
- Review and edit extracted markdown files
- Adjust html2text settings in `src/url_extractor.py`
