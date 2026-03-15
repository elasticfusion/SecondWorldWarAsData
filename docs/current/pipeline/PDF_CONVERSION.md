# PDF to Text Conversion

## Current Status

❌ **PDF conversion is NOT currently supported** in the pipeline.

The system expects markdown input files. However, there are several options to convert PDFs to markdown before processing.

## Recommended Workflow

### Quick Start

```bash
# 1. Install PyMuPDF (if not already installed)
pip install pymupdf

# 2. Convert PDF to markdown
python3 scripts/pdf_to_markdown.py document.pdf "BookName"

# 3. Edit metadata
vim contentrepository/BookName/chapter1/chapter1-meta.yaml

# 4. Review/clean content
vim contentrepository/BookName/chapter1/chapter1-content.md

# 5. Run pipeline
python3 phase1_parse.py
python3 phase2_extract.py
```

The `pdf_to_markdown.py` script is included in `scripts/` directory.

## PDF Conversion Tools

### 1. PyMuPDF (pymupdf/fitz) - Recommended

**Best for:** Text extraction with layout preservation

```bash
pip install pymupdf
```

**Usage:**
```python
import fitz  # PyMuPDF

def pdf_to_markdown(pdf_path, output_path):
    doc = fitz.open(pdf_path)
    markdown = []
    
    for page in doc:
        text = page.get_text()
        markdown.append(text)
    
    with open(output_path, 'w') as f:
        f.write('\n\n'.join(markdown))
```

**Pros:**
- ✅ Fast and reliable
- ✅ Preserves layout
- ✅ Handles images
- ✅ Good text extraction

**Cons:**
- ⚠️ May need manual cleanup
- ⚠️ Doesn't preserve markdown formatting

### 2. pdfplumber

**Best for:** Tables and structured data

```bash
pip install pdfplumber
```

**Usage:**
```python
import pdfplumber

def pdf_to_text(pdf_path, output_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = []
        for page in pdf.pages:
            text.append(page.extract_text())
    
    with open(output_path, 'w') as f:
        f.write('\n\n'.join(text))
```

**Pros:**
- ✅ Excellent table extraction
- ✅ Structured data handling
- ✅ Good for forms

**Cons:**
- ⚠️ Slower than PyMuPDF
- ⚠️ Plain text output

### 3. Marker (AI-powered)

**Best for:** Complex PDFs with formatting

```bash
pip install marker-pdf
```

**Usage:**
```bash
marker_single /path/to/file.pdf /output/dir --batch_multiplier 2
```

**Pros:**
- ✅ AI-powered conversion
- ✅ Preserves formatting
- ✅ Handles complex layouts
- ✅ Outputs markdown

**Cons:**
- ⚠️ Requires GPU for best performance
- ⚠️ Slower processing
- ⚠️ Larger dependencies

### 4. OCRmyPDF (for scanned PDFs)

**Best for:** Scanned documents

```bash
pip install ocrmypdf
```

**Usage:**
```bash
ocrmypdf input.pdf output.pdf
# Then use PyMuPDF to extract text
```

**Pros:**
- ✅ Handles scanned PDFs
- ✅ OCR capability
- ✅ Multiple languages

**Cons:**
- ⚠️ Requires Tesseract
- ⚠️ Slower processing
- ⚠️ OCR accuracy varies

### 5. Adobe PDF Services API

**Best for:** Production use, high quality

```bash
pip install pdfservices-sdk
```

**Pros:**
- ✅ High quality extraction
- ✅ Preserves structure
- ✅ Commercial support

**Cons:**
- ⚠️ Requires API key
- ⚠️ Costs money
- ⚠️ Cloud-based

## Creating a PDF Converter Script

Here's a simple script to add PDF support:

```python
#!/usr/bin/env python3
"""Convert PDF to markdown for pipeline processing."""

import sys
from pathlib import Path
import fitz  # PyMuPDF

def pdf_to_markdown(pdf_path: Path, output_dir: Path, book_name: str):
    """Convert PDF to markdown structure."""
    
    # Create output structure
    chapter_dir = output_dir / book_name / "chapter1"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract text from PDF
    doc = fitz.open(pdf_path)
    text_blocks = []
    
    for page_num, page in enumerate(doc, 1):
        text = page.get_text()
        # Add page markers
        text_blocks.append(f"[p. {page_num}]\n\n{text}")
    
    # Write content file
    content_file = chapter_dir / "chapter1-content.md"
    with open(content_file, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(text_blocks))
    
    # Create metadata template
    meta_file = chapter_dir / "chapter1-meta.yaml"
    with open(meta_file, 'w', encoding='utf-8') as f:
        f.write(f"""series: "TODO"
book: "{book_name}"
author: "TODO"
chapter_number: "1"
chapter_title: "TODO"
license: "TODO"
copyright_date: "TODO"
source_url: "TODO"
""")
    
    print(f"✅ Converted {pdf_path.name}")
    print(f"   Output: {chapter_dir}")
    print(f"   Next steps:")
    print(f"   1. Edit {meta_file}")
    print(f"   2. Review/clean {content_file}")
    print(f"   3. Run: python3 phase1_parse.py")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 pdf_to_markdown.py <pdf_file> <book_name>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    book_name = sys.argv[2]
    output_dir = Path("contentrepository")
    
    pdf_to_markdown(pdf_path, output_dir, book_name)
```

**Save as:** `scripts/pdf_to_markdown.py`

**Usage:**
```bash
python3 scripts/pdf_to_markdown.py document.pdf "BookName"
```

## Complete Workflow Example

### Step 1: Install Dependencies

```bash
pip install pymupdf
```

### Step 2: Convert PDF

```bash
python3 scripts/pdf_to_markdown.py paper.pdf "SmithPaper2024"
```

### Step 3: Edit Metadata

```bash
vim contentrepository/SmithPaper2024/chapter1/chapter1-meta.yaml
```

Update fields:
```yaml
series: "Journal of Military History"
book: "Logistical Analysis of D-Day"
author: "John Smith"
chapter_number: "1"
chapter_title: "Logistical Analysis of D-Day"
license: "CC-BY-4.0"
copyright_date: "2024"
source_url: "https://doi.org/..."
```

### Step 4: Review/Clean Content

```bash
vim contentrepository/SmithPaper2024/chapter1/chapter1-content.md
```

Clean up:
- Remove headers/footers
- Fix paragraph breaks
- Add footnote formatting: `[^1]: text`
- Fix image references

### Step 5: Run Pipeline

```bash
python3 phase1_parse.py
python3 phase2_extract.py
```

## Handling Common PDF Issues

### Issue: Poor Text Extraction

**Cause:** Scanned PDF or image-based PDF

**Solution:**
```bash
# Use OCR
pip install ocrmypdf
ocrmypdf input.pdf output.pdf
# Then extract text
```

### Issue: Broken Paragraphs

**Cause:** PDF line breaks

**Solution:** Manual cleanup or use regex:
```python
import re
text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)  # Join broken lines
```

### Issue: Tables Not Preserved

**Cause:** Complex table formatting

**Solution:** Use pdfplumber:
```python
import pdfplumber

with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        # Convert to markdown tables
```

### Issue: Footnotes Not Detected

**Cause:** PDF doesn't mark footnotes

**Solution:** Manual formatting:
```markdown
Text with citation.[^1]

[^1]: Citation text from footnote
```

### Issue: Images Lost

**Cause:** Images not extracted

**Solution:** Extract images separately:
```python
import fitz

doc = fitz.open(pdf_path)
for page_num, page in enumerate(doc):
    images = page.get_images()
    for img_index, img in enumerate(images):
        xref = img[0]
        base_image = doc.extract_image(xref)
        # Save image
```

## Advanced: Batch PDF Conversion

```python
#!/usr/bin/env python3
"""Batch convert PDFs to markdown."""

from pathlib import Path
import fitz

def batch_convert(pdf_dir: Path, output_dir: Path):
    """Convert all PDFs in directory."""
    
    for pdf_file in pdf_dir.glob("*.pdf"):
        book_name = pdf_file.stem
        print(f"Converting {pdf_file.name}...")
        
        try:
            pdf_to_markdown(pdf_file, output_dir, book_name)
        except Exception as e:
            print(f"❌ Error: {e}")
            continue

if __name__ == "__main__":
    pdf_dir = Path("pdfs")
    output_dir = Path("contentrepository")
    batch_convert(pdf_dir, output_dir)
```

## Quality Checklist

After PDF conversion:

- [ ] Metadata complete and accurate
- [ ] Paragraph breaks correct
- [ ] Footnotes formatted: `[^1]: text`
- [ ] Images referenced (if needed)
- [ ] Headers/footers removed
- [ ] Page numbers as markers: `[p. 123]`
- [ ] Tables readable (or converted to text)
- [ ] Special characters correct (é, ö, etc.)
- [ ] No extraction artifacts

## Future Enhancement

To add native PDF support to the pipeline:

1. **Add dependency:**
   ```bash
   echo "pymupdf" >> requirements.txt
   ```

2. **Create extraction module:**
   ```python
   # src/pdf_extractor.py
   def extract_pdf(pdf_path: Path) -> MarkdownDocument:
       # Convert PDF to MarkdownDocument
   ```

3. **Update discovery:**
   ```python
   # src/discovery.py
   # Look for .pdf files in addition to .md
   ```

4. **Update parser:**
   ```python
   # src/parser.py
   # Handle PDF input
   ```

## Alternatives to PDF

If you control the source:

1. **Request markdown/text** - Ask authors for source files
2. **Use LaTeX source** - Convert .tex to markdown
3. **Use Word/Google Docs** - Export as markdown
4. **Use HTML** - Already supported via `extract_url.py`

## Related Documentation

- [ADDING_DATA_SOURCES.md](ADDING_DATA_SOURCES.md) - Adding content
- [PAPERS_AND_ARTICLES.md](PAPERS_AND_ARTICLES.md) - Handling papers
- [DEVELOPMENT.md](../core/DEVELOPMENT.md) - Development guide

## Summary

**Current:** ❌ No native PDF support  
**Workaround:** ✅ Convert PDF → Markdown → Pipeline  
**Best tool:** PyMuPDF (pymupdf)  
**Effort:** ~15 minutes per document (conversion + cleanup)  
**Quality:** Good with manual review
