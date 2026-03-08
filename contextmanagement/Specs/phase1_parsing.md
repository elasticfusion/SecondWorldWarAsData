# Phase 1: Markdown Parsing

**Version:** 1.0.0  
**Status:** Active  
**Last Updated:** 2026-02-23

---

## Overview

Phase 1 discovers and parses WWII book markdown files from the content repository into structured JSON format for downstream extraction.

---

## Architecture

### Input Structure

```
contentrepository/
├── BreakoutAndPursuit/
│   ├── chapter1a.md
│   ├── chapter1a-meta.yaml
│   ├── chapter1b.md
│   ├── chapter1b-meta.yaml
│   └── chapter2.md
└── CrossChannelAttack/
    ├── chapter1.md
    └── chapter1-meta.yaml
```

### Output Structure

```
output/
├── BreakoutAndPursuit/
│   ├── chapter1a-parsed.json
│   ├── chapter1b-parsed.json
│   └── chapter2full-parsed.json
└── CrossChannelAttack/
    └── chapter1full-parsed.json
```

---

## Components

### 1. Configuration Loading

**Module:** `src.utils.config`

**Function:**
```python
config = load_config(base_dir / "config.yaml")
paths = get_paths(config, base_dir)
```

**Loads:**
- Content repository path
- Output directory path
- Logging configuration
- Processing options

**Configuration Example:**
```yaml
paths:
  content_root: "contentrepository"
  output_root: "output"

logging:
  level: "INFO"
  console: true
  file: "logs/phase1.log"
```

---

### 2. Content Discovery

**Module:** `src.discovery`

**Function:**
```python
structure = discover_content_structure(content_root)
```

**Returns:**
```python
{
  "BreakoutAndPursuit": [
    ChapterGroup(
      chapter_number=1,
      content_files={"a": Path("chapter1a.md"), "b": Path("chapter1b.md")},
      metadata_file=Path("chapter1a-meta.yaml")
    ),
    ChapterGroup(
      chapter_number=2,
      content_files={"": Path("chapter2.md")},
      metadata_file=Path("chapter2-meta.yaml")
    )
  ]
}
```

**Discovery Logic:**
1. Scan content_root for book directories
2. Find markdown files matching `chapter{N}[a-z]?.md`
3. Group by chapter number
4. Identify section suffixes (a, b, c, etc.)
5. Locate corresponding metadata files

**Supported Patterns:**
- `chapter1.md` - Single file chapter
- `chapter1a.md`, `chapter1b.md` - Multi-section chapter
- `chapter19full.md` - Explicit full chapter

---

### 3. Metadata Loading

**Format:** YAML

**Required Fields:**
- `book` - Book title
- `author` - Author name
- `chapter_title` - Chapter title

**Optional Fields:**
- `series` - Book series name
- `license` - Content license

**Example:**
```yaml
book: "Breakout and Pursuit"
author: "Martin Blumenson"
series: "United States Army in World War II"
chapter_title: "The Allies"
license: "Public Domain"
```

---

### 4. Markdown Parsing

**Module:** `src.parser`

**Function:**
```python
documents = parse_chapter(chapter_group)
```

**Parsing Steps:**

#### A. Extract Page Markers
```markdown
<span id="page-1">p. 1</span>
```
→ Maps text position to page number

#### B. Parse Paragraphs
- Split by double newlines
- Track absolute paragraph number (continuous across sections)
- Assign page numbers based on position
- Preserve section ID

#### C. Extract Images
```markdown
![Alt text](https://example.com/image.jpg)
```
→ Captures URL, alt text, caption

#### D. Extract Maps
```markdown
[Map I](https://example.com/map1.jpg)
```
→ Captures URL, description, map ID

#### E. Extract Footnotes
```markdown
<sup>[1](https://example.com/footnote1.html)</sup>
```
→ Captures number and URL

---

### 5. Document Structure

**Class:** `MarkdownDocument`

**Fields:**
```python
{
  "book": str,              # Book title
  "chapter_number": int,    # Chapter number
  "chapter_title": str,     # Chapter title
  "section_id": str,        # Section suffix (a, b, c) or ""
  "author": str,            # Author name
  "series": str,            # Series name
  "license": str,           # Content license
  "source_file": str,       # Original markdown file path
  "paragraphs": [           # List of paragraphs
    {
      "absolute_number": int,    # Continuous numbering
      "text": str,               # Paragraph text
      "page_number": int,        # Source page number
      "section_id": str,         # Section this belongs to
      "source_file": str         # Source markdown file
    }
  ],
  "images": [               # List of images
    {
      "type": str,               # "image"
      "resource_id": str,        # Unique ID
      "url": str,                # Image URL
      "alt_text": str,           # Alt text
      "caption": str             # Caption if any
    }
  ],
  "maps": [                 # List of maps
    {
      "url": str,                # Map URL
      "description": str,        # Map description
      "map_id": str              # Map identifier (I, II, III)
    }
  ],
  "footnotes": [            # List of footnotes
    {
      "number": int,             # Footnote number
      "url": str                 # Footnote URL
    }
  ]
}
```

---

### 6. JSON Serialization

**Output Format:** JSON with 2-space indentation

**Encoding:** UTF-8 with Unicode characters preserved

**File Naming:**
- Pattern: `chapter{N}{section}-parsed.json`
- Examples:
  - `chapter1a-parsed.json` - Chapter 1, section a
  - `chapter2full-parsed.json` - Chapter 2, single file
  - `chapter19full-parsed.json` - Chapter 19, single file

---

## Features

### 1. Multi-Section Support

**Handles chapters split across multiple files:**
- `chapter1a.md` + `chapter1b.md` + `chapter1c.md`
- Each section parsed separately
- Continuous paragraph numbering across sections

**Example:**
```
chapter1a.md: paragraphs 1-50
chapter1b.md: paragraphs 51-100
chapter1c.md: paragraphs 101-150
```

### 2. Page Number Tracking

**Preserves original page numbers:**
- Extracts page markers from markdown
- Maps text positions to pages
- Assigns page numbers to paragraphs

**Benefits:**
- Citation accuracy
- Source verification
- Cross-reference validation

### 3. Resource Extraction

**Captures all embedded resources:**
- Images with alt text and captions
- Maps with descriptions and IDs
- Footnotes with numbers and URLs

**Use cases:**
- Image analysis
- Map georeferencing
- Footnote resolution

### 4. Metadata Enrichment

**Adds book context to every document:**
- Book title, author, series
- Chapter title and number
- Content license
- Source file path

**Benefits:**
- Self-contained documents
- Citation generation
- Provenance tracking

### 5. Absolute Paragraph Numbering

**Continuous numbering across sections:**
- Enables cross-section references
- Simplifies event extraction
- Maintains document structure

---

## Data Flow

```
┌─────────────────────┐
│ Markdown Files      │
│ + Metadata YAML     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ discover_content_   │
│ structure()         │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ ChapterGroup        │
│ objects             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ parse_chapter()     │
│ - Extract pages     │
│ - Parse paragraphs  │
│ - Extract resources │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ MarkdownDocument    │
│ objects             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ JSON Serialization  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Parsed JSON Files   │
└─────────────────────┘
```

---

## Usage

### Basic Usage

```bash
python3 phase1_parse.py
```

### With Logging

```bash
python3 phase1_parse.py --log-level DEBUG
```

### Output

```
Starting Phase 1: File Discovery and Parsing
Scanning content directory: contentrepository
Found 2 book(s)
  BreakoutAndPursuit: 3 chapter(s)
    Chapter 1: sections [a, b]
    Chapter 2: sections [single file]
  CrossChannelAttack: 1 chapter(s)
    Chapter 1: sections [single file]
Parsing BreakoutAndPursuit - Chapter 1
  Saved: chapter1a-parsed.json (50 paragraphs)
  Saved: chapter1b-parsed.json (45 paragraphs)
Parsing BreakoutAndPursuit - Chapter 2
  Saved: chapter2full-parsed.json (60 paragraphs)
Parsing CrossChannelAttack - Chapter 1
  Saved: chapter1full-parsed.json (55 paragraphs)
Phase 1 complete!
```

---

## Error Handling

### Missing Metadata

**Error:**
```
FileNotFoundError: Metadata file not found for chapter1a.md
```

**Solution:**
- Create `chapter1a-meta.yaml` with required fields
- Ensure metadata file matches content file name

### Invalid Markdown

**Error:**
```
ParsingError: Failed to parse chapter1a.md
```

**Solution:**
- Check markdown syntax
- Verify page markers are properly formatted
- Ensure UTF-8 encoding

### Missing Required Fields

**Error:**
```
KeyError: 'book' not found in metadata
```

**Solution:**
- Add required fields to metadata YAML
- Required: `book`, `author`, `chapter_title`

---

## Output Validation

### Paragraph Count

**Check:**
```bash
jq '.paragraphs | length' output/BreakoutAndPursuit/chapter1a-parsed.json
```

### Metadata Presence

**Check:**
```bash
jq '{book, author, series}' output/BreakoutAndPursuit/chapter1a-parsed.json
```

### Page Numbers

**Check:**
```bash
jq '.paragraphs[] | select(.page_number == null)' output/BreakoutAndPursuit/chapter1a-parsed.json
```

---

## Performance

### Typical Processing Time

- **Single chapter:** 0.1-0.5 seconds
- **Full book (20 chapters):** 2-10 seconds
- **Entire corpus (5 books):** 10-50 seconds

### Memory Usage

- **Per chapter:** ~1-5 MB
- **Peak memory:** ~50-100 MB

---

## Limitations

### 1. Markdown Format Dependency

**Requires specific format:**
- Page markers: `<span id="page-N">p. N</span>`
- Images: Standard markdown syntax
- Footnotes: Superscript with links

**Not supported:**
- Custom markdown extensions
- HTML tables
- Embedded videos

### 2. No Content Validation

**Does not validate:**
- Historical accuracy
- Date formats
- Place names
- Person names

**Validation happens in Phase 2** (extraction)

### 3. No Deduplication

**Does not handle:**
- Duplicate chapters
- Overlapping sections
- Conflicting metadata

**Manual cleanup required** before parsing

---

## Next Steps

After Phase 1 completes:

1. **Verify output** - Check parsed JSON files
2. **Run Phase 2** - Extract events, dates, places, people
3. **Review logs** - Check for parsing warnings

**Command:**
```bash
python3 phase2_extract.py
```

---

## Related Documentation

- **Phase 2:** `contextmanagement/Specs/phase2_extraction.md` (TODO)
- **Configuration:** `config.yaml`
- **Discovery:** `src/discovery.py`
- **Parser:** `src/parser.py`

---

## Future Enhancements

1. **Parallel Processing** - Parse multiple chapters simultaneously
2. **Incremental Parsing** - Only parse changed files
3. **Format Validation** - Validate markdown structure
4. **Content Preview** - Generate HTML previews
5. **Statistics** - Report parsing metrics
6. **Error Recovery** - Continue on partial failures
7. **Custom Extractors** - Plugin system for custom markdown formats

---

**Status:** ✅ Production Ready
