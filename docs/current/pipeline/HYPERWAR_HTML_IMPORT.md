# HyperWar HTML Import

Import books from [ibiblio.org/hyperwar](https://www.ibiblio.org/hyperwar/) into the pipeline content repository.

## Requirements

The following Python packages are required (all included in `requirements.txt`):

- `requests` — HTTP downloads
- `beautifulsoup4` — HTML parsing
- `html2text` — HTML to markdown conversion
- `pyyaml` — Metadata YAML generation

Verify installation:

```bash
source .venv/bin/activate
pip install requests beautifulsoup4 html2text pyyaml
```

## Usage

```bash
python3 scripts/import_hyperwar_html.py <index_url>
```

### Example

```bash
python3 scripts/import_hyperwar_html.py \
  https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html
```

The script will:

1. Parse the index page to discover all chapter and appendix links
2. Prompt you for book metadata (series, title, author, license, etc.)
3. Download each chapter HTML page
4. Convert HTML to markdown preserving footnotes, images, and page markers
5. Split chapters into sub-chapters at section headings (`###`)
6. Write files to `contentrepository/<BookName>/` matching the existing format

### Metadata Prompts

You will be asked for:

| Field | Default | Required |
|-------|---------|----------|
| Series | United States Army in World War II | No |
| Book title | — | Yes |
| Author | — | Yes |
| License | Public Domain | No |
| Copyright year | — | No |
| Directory name | Derived from book title | No |

## Output Structure

The script creates the standard content repository layout:

```
contentrepository/CrossChannelAttack/
├── chapter1/
│   ├── chapter1-meta.yaml
│   ├── chapter1a-content.md    # Chapter intro / first section
│   ├── chapter1b-content.md    # Second section
│   ├── chapter1c-content.md    # Third section
│   └── ...
├── chapter2/
│   ├── chapter2-meta.yaml
│   └── ...
└── ...
```

### Sub-Chapter Splitting

Chapters are automatically split into sub-chapter files at `###` section headings found in the HTML. For example, Chapter I of *Cross-Channel Attack* splits into:

| File | Section |
|------|---------|
| `chapter1a-content.md` | The Roots of Strategy (intro) |
| `chapter1b-content.md` | General Marshall's Project |
| `chapter1c-content.md` | "Action in 1942--Not 1943" |
| `chapter1d-content.md` | The Period of Indecision |
| `chapter1e-content.md` | The Casablanca Conference |

Footnotes are kept with the last sub-chapter rather than split into a separate file.

### Metadata Format

Generated `chapter*-meta.yaml` files match the existing format:

```yaml
series: United States Army in World War II
book: Cross-Channel Attack
author: Gordon A. Harrison
chapter_number: 1
chapter_title: THE ROOTS OF STRATEGY
license: Public Domain
copyright_date: '1951'
source_url: https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html
```

### Content Format

Markdown content is blockquoted to match the existing style:

```markdown
> ### _General Marshall's Project_
>
> The first look at the cross-Channel project discovered only a host
> of difficulties that seemed all but insuperable...
```

## After Import

Run the standard pipeline:

```bash
# 1. Parse markdown to JSON
python3 phase1_parse.py

# 2. Extract entities
python3 phase2_retry.py
```

## Supported Sources

The script is designed for HyperWar index pages that follow the standard table-of-contents format with chapter links. Tested with:

- [Cross-Channel Attack](https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html) — 10 chapters + 10 appendices
- [Breakout and Pursuit](https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/index.html) — source of existing content

Other HyperWar books using the same HTML structure should work without modification.

## Troubleshooting

### Paragraphs merged into single lines

Early versions of the import produced content where multiple `<p>` tags inside `<blockquote>` elements were collapsed into one long line by `html2text`. This was fixed by inserting `<br><br>` between block-level children (`<p>`, `<center>`) of each `<blockquote>` before conversion. If you see merged paragraphs in existing content, re-import the affected book.

### No chapters found

The index page format may differ. Check that the page has `<a href="...">` links pointing to chapter HTML files. The script filters out links to maps, images, charts, and glossary pages.

### Missing section splits

Section headings must be `<h3>` tags in the source HTML. If a chapter has no `<h3>` headings, it will be written as a single `chapter*a-content.md` file.

### Character encoding issues

The script uses the encoding reported by the server. If you see garbled characters, the source page may use a non-UTF-8 encoding. Open an issue with the URL.

## See Also

- [Adding Data Sources](ADDING_DATA_SOURCES.md) — General guide for adding content
- [PDF Conversion](PDF_CONVERSION.md) — Import from PDF files
- [Papers and Articles](PAPERS_AND_ARTICLES.md) — Import shorter documents
