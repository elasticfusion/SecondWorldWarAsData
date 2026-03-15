# Handling Papers and Articles

## Overview

The pipeline is designed for chapter-based books but can handle papers, articles, and other non-chapter documents with minimal adaptation.

## Current System Design

The system expects:
- **Book/Chapter structure**: `contentrepository/BookName/chapterN/`
- **Chapter naming**: `chapter1`, `chapter2`, etc.
- **Metadata per chapter**: `chapter*-meta.yaml`

## Adapting for Papers/Articles

### Option 1: Treat Paper as Single Chapter (Recommended)

Use the existing structure with paper as a "chapter":

```
contentrepository/
└── PaperCollection/
    └── chapter1/
        ├── chapter1-meta.yaml
        └── chapter1-content.md
```

**Metadata:**
```yaml
series: "Journal Name" or "Conference Proceedings"
book: "Paper Title"
author: "Author Name(s)"
chapter_number: "1"
chapter_title: "Paper Title"
license: "CC-BY-4.0" or appropriate
copyright_date: "2024"
source_url: "https://doi.org/..."
```

**Advantages:**
- ✅ Works with existing pipeline
- ✅ No code changes needed
- ✅ Supports all features (footnotes, bibliography)

**Example:**
```bash
# Create structure
mkdir -p contentrepository/SmithPaper2024/chapter1

# Create metadata
cat > contentrepository/SmithPaper2024/chapter1/chapter1-meta.yaml << 'EOF'
series: "Historical Studies Quarterly"
book: "The Impact of D-Day on European Theater"
author: "John Smith"
chapter_number: "1"
chapter_title: "The Impact of D-Day on European Theater"
license: "CC-BY-4.0"
copyright_date: "2024"
source_url: "https://doi.org/10.1234/example"
EOF

# Add content
cat > contentrepository/SmithPaper2024/chapter1/chapter1-content.md << 'EOF'
# Abstract

This paper examines...

# Introduction

The D-Day invasion...

# Methodology

We analyzed...

# Results

Our findings show...

# Conclusion

In conclusion...

# References

[^1]: Eisenhower, D. (1948). Crusade in Europe.
[^2]: Bradley, O. (1951). A Soldier's Story.
EOF

# Run pipeline
python3 phase1_parse.py
python3 phase2_extract.py
```

### Option 2: Multiple Papers as Chapters

Treat each paper as a chapter in a collection:

```
contentrepository/
└── ConferenceProceedings2024/
    ├── chapter1/  # Paper 1
    │   ├── chapter1-meta.yaml
    │   └── chapter1-content.md
    ├── chapter2/  # Paper 2
    │   ├── chapter2-meta.yaml
    │   └── chapter2-content.md
    └── chapter3/  # Paper 3
        ├── chapter3-meta.yaml
        └── chapter3-content.md
```

**Metadata per paper:**
```yaml
series: "WWII History Conference 2024"
book: "Conference Proceedings"
author: "Various Authors"
chapter_number: "1"
chapter_title: "Individual Paper Title"
license: "CC-BY-4.0"
copyright_date: "2024"
source_url: "https://..."
```

### Option 3: Sections as Chapters

For long papers with distinct sections:

```
contentrepository/
└── LongPaper/
    ├── chapter1/  # Introduction
    │   ├── chapter1-meta.yaml
    │   └── chapter1-content.md
    ├── chapter2/  # Literature Review
    │   ├── chapter2-meta.yaml
    │   └── chapter2-content.md
    └── chapter3/  # Analysis
        ├── chapter3-meta.yaml
        └── chapter3-content.md
```

## Handling Special Elements

### Footnotes and Endnotes

**Already supported** - Use standard markdown syntax:

```markdown
This is a statement with a citation.[^1]

Another statement with a reference.[^2]

[^1]: Smith, J. (2024). Historical Analysis. Journal, 10(2), 45-67.
[^2]: Jones, M. (2023). Military Strategy. Publisher.
```

The parser automatically extracts:
- Footnote numbers
- Footnote text
- Links to paragraph numbers

### Bibliography/References

**Two approaches:**

#### 1. As Footnotes (Recommended)
```markdown
# References

[^1]: Eisenhower, D. (1948). Crusade in Europe. New York: Doubleday.
[^2]: Bradley, O. (1951). A Soldier's Story. New York: Henry Holt.
[^3]: Montgomery, B. (1958). Memoirs. London: Collins.
```

**Advantages:**
- ✅ Automatically extracted
- ✅ Linked to citations in text
- ✅ Preserved in parsed output

#### 2. As Regular Paragraphs
```markdown
# References

Eisenhower, D. (1948). Crusade in Europe. New York: Doubleday.

Bradley, O. (1951). A Soldier's Story. New York: Henry Holt.

Montgomery, B. (1958). Memoirs. London: Collins.
```

**Advantages:**
- ✅ Simple format
- ✅ Preserved as paragraphs
- ⚠️ Not linked to citations

### Abstract

Include as first section:

```markdown
# Abstract

This paper examines the impact of D-Day...

# Introduction

The Normandy invasion...
```

### Acknowledgments

Include as final section:

```markdown
# Acknowledgments

The author thanks...

# References

[^1]: ...
```

## Metadata Considerations

### Series Field

Use for journal/conference/collection:
- `"Journal of Military History"`
- `"WWII Conference Proceedings 2024"`
- `"Historical Studies Collection"`
- `"Standalone Paper"` (if not part of series)

### Book Field

Use for paper title:
- `"The Impact of D-Day on European Theater"`
- `"Strategic Analysis of Operation Overlord"`

### Author Field

Multiple authors:
```yaml
author: "John Smith, Jane Doe, Robert Johnson"
```

Or use full citation format:
```yaml
author: "Smith, J., Doe, J., & Johnson, R."
```

### License Field

Common licenses for papers:
- `"CC-BY-4.0"` - Creative Commons Attribution
- `"CC-BY-SA-4.0"` - Creative Commons Share-Alike
- `"CC-BY-NC-4.0"` - Creative Commons Non-Commercial
- `"Public Domain"`
- `"All Rights Reserved"`
- `"Fair Use"` (for excerpts)

### Source URL

Use DOI when available:
```yaml
source_url: "https://doi.org/10.1234/example"
```

Or journal URL:
```yaml
source_url: "https://journal.org/article/12345"
```

## Complete Example: Academic Paper

```bash
# Create structure
mkdir -p contentrepository/JonesAnalysis2023/chapter1

# Create metadata
cat > contentrepository/JonesAnalysis2023/chapter1/chapter1-meta.yaml << 'EOF'
series: "Journal of Military History"
book: "Logistical Challenges in Operation Overlord"
author: "Sarah Jones, PhD"
chapter_number: "1"
chapter_title: "Logistical Challenges in Operation Overlord"
license: "CC-BY-4.0"
copyright_date: "2023"
source_url: "https://doi.org/10.5678/jmh.2023.456"
EOF

# Create content
cat > contentrepository/JonesAnalysis2023/chapter1/chapter1-content.md << 'EOF'
# Abstract

This paper examines the logistical challenges faced during Operation Overlord, 
focusing on supply chain management and resource allocation.[^1]

# Introduction

The success of D-Day depended heavily on logistics.[^2] Previous studies have 
examined tactical aspects,[^3] but logistical challenges remain understudied.

# Methodology

We analyzed primary sources from the National Archives, including:
- Supply manifests
- Transportation records
- Command communications

# Analysis

## Supply Chain Management

The Allies faced unprecedented challenges in coordinating supplies across the 
English Channel.[^4]

## Resource Allocation

Critical resources included fuel, ammunition, and medical supplies.[^5]

# Conclusion

Our analysis reveals that logistical planning was as critical as tactical 
execution in the success of Operation Overlord.

# Acknowledgments

The author thanks the National Archives staff for their assistance.

# References

[^1]: Jones, S. (2023). Logistical Challenges in Operation Overlord. 
      Journal of Military History, 87(2), 234-267.
[^2]: Eisenhower, D. (1948). Crusade in Europe. New York: Doubleday.
[^3]: Bradley, O. (1951). A Soldier's Story. New York: Henry Holt.
[^4]: Ruppenthal, R. (1953). Logistical Support of the Armies. 
      Washington: US Army.
[^5]: Coakley, R. & Leighton, R. (1955). Global Logistics and Strategy. 
      Washington: US Army.
EOF

# Run pipeline
python3 phase1_parse.py
python3 phase2_extract.py
```

## Output Structure

After processing:

```
output/
└── JonesAnalysis2023/
    ├── chapter1-parsed.json      # Parsed content with footnotes
    ├── chapter1-event.json       # Extracted events
    ├── chapter1-dates.json       # Temporal entities
    ├── chapter1-places.json      # Geographic entities
    └── ...
```

## Validation

```bash
# Check parsed output
jq '.metadata' output/JonesAnalysis2023/chapter1-parsed.json

# Check footnotes
jq '.footnotes | length' output/JonesAnalysis2023/chapter1-parsed.json

# View footnotes
jq '.footnotes' output/JonesAnalysis2023/chapter1-parsed.json
```

## Limitations

### Current System
- ✅ Handles footnotes/endnotes
- ✅ Preserves bibliography
- ✅ Extracts citations
- ⚠️ Requires "chapter" naming convention
- ⚠️ No special handling for abstracts
- ⚠️ No citation parsing (preserved as text)

### Workarounds
- Use `chapter1` for single papers
- Use `chapter_title` for paper title
- Include abstract as first section
- Format bibliography as footnotes for linking

## Future Enhancements

Potential improvements for better paper support:

1. **Flexible naming**: Accept `paper1`, `article1`, etc.
2. **Abstract extraction**: Separate abstract field
3. **Citation parsing**: Extract structured citations
4. **Author metadata**: Multiple authors with affiliations
5. **Keywords**: Extract and index keywords
6. **DOI handling**: Automatic DOI validation

## Best Practices

### For Single Papers
1. Use descriptive directory name (author + year)
2. Set `series` to journal/conference name
3. Set `book` to paper title
4. Include DOI in `source_url`
5. Format bibliography as footnotes

### For Paper Collections
1. Use collection name as directory
2. Each paper as separate chapter
3. Consistent metadata across papers
4. Include collection-level README

### For Long Papers
1. Split by major sections if needed
2. Use continuous paragraph numbering
3. Keep bibliography in final section
4. Cross-reference sections in metadata

## Related Documentation

- [ADDING_DATA_SOURCES.md](ADDING_DATA_SOURCES.md) - Adding new content
- [PIPELINE.md](../core/PIPELINE.md) - Pipeline overview
