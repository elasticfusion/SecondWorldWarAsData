# Adding New Data Sources

## Overview

The pipeline is designed to handle multiple books from the same series or different series. Adding a new data source is straightforward.

## Process

### 1. Prepare Content

Create directory structure in `contentrepository/`:

```
contentrepository/
└── NewBookName/
    ├── chapter1/
    │   ├── chapter1-meta.yaml
    │   └── chapter1-content.md
    ├── chapter2/
    │   ├── chapter2-meta.yaml
    │   └── chapter2-content.md
    └── ...
```

### 2. Create Metadata Files

Each chapter needs a `chapter*-meta.yaml`:

```yaml
series: "United States Army in World War II"
book: "New Book Name"
author: "Author Name"
chapter_number: "I"
chapter_title: "Chapter Title"
license: "Public Domain"
copyright_date: "1961"
source_url: "https://..."
```

**Quick method:**
```bash
# Generate templates automatically
python3 scripts/generate_missing_metadata.py

# Then complete with Grok
python3 scripts/complete_metadata_with_grok.py
```

### 3. Create Content Files

Each chapter needs a `chapter*-content.md` with markdown text:

```markdown
# Chapter Title

Paragraph 1 text here.

Paragraph 2 text here.

[^1]: Footnote text

![Image caption](image.jpg "Image title")

[MAP: Map description]

[p. 123]
```

**From URL:**
```bash
python3 scripts/extract_url.py https://example.com/book
```

### 4. Run Pipeline

```bash
# Parse new content
python3 phase1_parse.py

# Extract entities
python3 phase2_extract.py
```

The pipeline will:
- Discover new book automatically
- Parse all chapters
- Extract events, dates, places, people, groups
- Merge with existing people/groups across books
- Generate reports

## Content Requirements

### Metadata Fields

**Required:**
- `series` - Book series name
- `book` - Book title
- `author` - Author name
- `license` - License type

**Optional (auto-completed):**
- `chapter_number` - Chapter number (I, II, 1, 2, etc.)
- `chapter_title` - Chapter title
- `copyright_date` - Copyright year
- `source_url` - Original source URL

### Content Format

**Supported markdown:**
- Paragraphs (separated by blank lines)
- Footnotes: `[^1]: text`
- Images: `![alt](url "caption")`
- Maps: `[MAP: description]`
- Page markers: `[p. 123]`

**Not required:**
- Headers (extracted from metadata)
- Tables (preserved as text)
- Lists (preserved as text)

## Cross-Book Integration

### People Tracking

People are automatically tracked across books:

```json
{
  "PersonID": "01ABC...",
  "name": "Dwight D. Eisenhower",
  "event_mentions": [
    {
      "book": "Breakout and Pursuit",
      "author": "Martin Blumenson",
      "series": "United States Army in World War II"
    },
    {
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II"
    }
  ]
}
```

### People Groups Tracking

Groups are automatically tracked across books:

```json
{
  "GroupID": "01DEF...",
  "group_name": "Wehrmacht",
  "event_mentions": [
    {
      "book": "Breakout and Pursuit",
      ...
    },
    {
      "book": "Cross-Channel Attack",
      ...
    }
  ]
}
```

### Deduplication

After adding new content:

```bash
# Find duplicates
python3 scripts/find_duplicate_people.py

# Review and merge
python3 scripts/merge_duplicate_people.py

# Find related groups
python3 scripts/find_related_groups.py

# Suggest aliases
python3 scripts/suggest_group_aliases.py

# Consolidate
python3 scripts/consolidate_people_groups.py
```

## Example: Adding a New Book

### Step-by-step

```bash
# 1. Create directory
mkdir -p contentrepository/NewBook/chapter1

# 2. Create metadata
cat > contentrepository/NewBook/chapter1/chapter1-meta.yaml << 'EOF'
series: "United States Army in World War II"
book: "New Book"
author: "Author Name"
license: "Public Domain"
copyright_date: "1950"
source_url: "https://example.com"
EOF

# 3. Add content
cat > contentrepository/NewBook/chapter1/chapter1-content.md << 'EOF'
This is the first paragraph.

This is the second paragraph.
EOF

# 4. Run pipeline
python3 phase1_parse.py
python3 phase2_extract.py

# 5. Review results
ls output/NewBook/
# chapter1-parsed.json
# chapter1-event.json
# chapter1-dates.json
# chapter1-places.json

ls output/people/
# {Name}_{PersonID}.json (merged with existing)

ls output/people_groups/
# {Group}_{GroupID}.json (merged with existing)
```

## Bulk Import

For multiple books:

```bash
# 1. Organize content
contentrepository/
├── Book1/
├── Book2/
└── Book3/

# 2. Generate all metadata
python3 scripts/generate_missing_metadata.py

# 3. Complete metadata
python3 scripts/complete_metadata_with_grok.py

# 4. Run pipeline (processes all books)
python3 phase1_parse.py
python3 phase2_extract.py

# 5. Deduplicate
python3 scripts/merge_duplicate_people.py
python3 scripts/consolidate_people_groups.py
```

## Data Source Types

### Supported Sources

**Public domain books:**
- US Army official histories
- Government publications
- Historical documents

**Web sources:**
```bash
python3 scripts/extract_url.py https://example.com/book
```

**Manual transcription:**
- Create markdown files directly
- Add metadata manually

### Licensing

Ensure content is:
- Public domain
- Properly licensed
- Attribution included in metadata

## Output Structure

After adding new content:

```
output/
├── NewBook/
│   ├── chapter1-parsed.json
│   ├── chapter1-event.json
│   ├── chapter1-dates.json
│   └── chapter1-places.json
├── people/
│   ├── index.json (updated)
│   ├── {existing people updated}
│   └── {new people added}
└── people_groups/
    ├── index.json (updated)
    ├── {existing groups updated}
    └── {new groups added}
```

## Validation

### Check Parsing

```bash
# Verify parsed output
jq '.metadata' output/NewBook/chapter1-parsed.json

# Check paragraph count
jq '.paragraphs | length' output/NewBook/chapter1-parsed.json
```

### Check Extraction

```bash
# Verify events
jq '.events | length' output/NewBook/chapter1-event.json

# Check people
ls output/people/*.json | wc -l

# Check groups
ls output/people_groups/*.json | wc -l
```

### Review Cache

```bash
# Inspect API calls
python3 scripts/review_cache.py
```

## Troubleshooting

### Issue: Metadata Not Found

**Solution:**
```bash
python3 scripts/generate_missing_metadata.py
```

### Issue: Chapter Title Missing

**Solution:**
```bash
python3 scripts/complete_metadata_with_grok.py
```

### Issue: Duplicate People Not Detected

**Solution:**
```bash
# Regenerate report
python3 scripts/find_duplicate_people.py

# Review and merge
python3 scripts/merge_duplicate_people.py
```

### Issue: Wrong Book/Author in Events

**Solution:**
```bash
# Fix metadata
vim contentrepository/NewBook/chapter1/chapter1-meta.yaml

# Regenerate events
rm output/NewBook/chapter1-event.json
python3 phase2_extract.py
```

## Best Practices

### Content Preparation

1. **Verify source license** - Ensure public domain or proper licensing
2. **Clean formatting** - Remove extra whitespace, fix encoding
3. **Complete metadata** - Fill all required fields
4. **Test with one chapter** - Verify pipeline before bulk import

### Quality Assurance

1. **Review parsed output** - Check paragraph numbering
2. **Verify events** - Ensure proper extraction
3. **Check people** - Review biographical profiles
4. **Validate places** - Verify coordinates
5. **Deduplicate** - Merge duplicates across books

### Performance

1. **Use caching** - Don't clear cache unnecessarily
2. **Process incrementally** - Add books one at a time
3. **Monitor API usage** - Check cache hit rates
4. **Batch operations** - Process multiple chapters together

## Next Steps

After adding new content:

1. **Review extraction quality** - Check events, people, places
2. **Deduplicate entities** - Merge duplicates
3. **Consolidate groups** - Apply aliases
4. **Update documentation** - Add new books to README
5. **Backup data** - Save output and cache

## Related Documentation

- [PIPELINE.md](PIPELINE.md) - Pipeline overview
- [METADATA.md](METADATA.md) - Metadata system
- [PEOPLE_MANAGEMENT.md](PEOPLE_MANAGEMENT.md) - People tracking
- [PEOPLE_GROUPS.md](PEOPLE_GROUPS.md) - Group management
