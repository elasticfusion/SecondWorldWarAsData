# Design: Split Supplemental into Bibliography + Factual Content

## Problem
Endnotes/footnotes currently mix two distinct types:
1. **Document/book references** — citations to source materials (books, memos, AARs, field orders)
2. **Factual statements** — historical narrative content (casualties, awards, troop movements)

Both are dumped into `output/supplemental/` with no distinction. Factual content contains extractable entities (people, dates, places, equipment) that are being lost.

## Design

### Classification
Grok classifies each endnote/footnote as:
- `document_reference` — pure citation (book, memo, AAR, field order, etc.)
- `factual_content` — historical narrative with extractable entities
- `ambiguous` — unclear; queued for human review

Mixed entries (e.g., a factual statement followed by a citation) are split into separate entries.

### Document References → `output/bibliography/`
Pattern matches `output/people/` and `output/equipment/`:
- One JSON file per unique document/book: `{title_slug}_{ULID}.json`
- `index.json` mapping normalized titles to filenames
- Deduplicated across chapters and books
- Each file tracks all mentions from any chapter in any book:

```json
{
  "BibliographyID": "01XXXX...",
  "title": "First U.S. Army, Report of Operations",
  "citation": {
    "author": ["First U.S. Army"],
    "publisher": "...",
    "publication_date": "...",
    "publication_location": "...",
    "publication_country": "...",
    "isbn": null,
    "pages": null,
    "volume": null,
    "edition": null,
    "translator": null,
    "periodical_name": null,
    "document_type": "Primary source",
    "author_death_date": null
  },
  "availability": "archive",
  "resource_urls": [],
  "archive_reference_number": "...",
  "archive_physical_address": "NARA, College Park, MD, USA",
  "license": "public_domain",
  "license_notes": "US Government work",
  "mentions": [
    {
      "MentionID": "01XXXX...",
      "EventID": "...",
      "Sub-eventID": "...",
      "book": "Breakout and Pursuit",
      "chapter": "chapter3a",
      "reference_type": "endnote",
      "reference_number": "21",
      "verbatim_reference": "First U.S. Army, Report of Operations, I, 80",
      "pages": "80",
      "volume": "I"
    },
    {
      "MentionID": "01YYYY...",
      "EventID": "...",
      "Sub-eventID": "...",
      "book": "Cross-Channel Attack",
      "chapter": "chapter8b",
      "reference_type": "endnote",
      "reference_number": "44",
      "verbatim_reference": "First U.S. Army, Report of Operations, I, 121-22",
      "pages": "121-22",
      "volume": "I"
    }
  ]
}
```

A single bibliography file may accumulate mentions from multiple chapters within one book, or across entirely different books. The `mentions` array is the complete provenance trail.

### Factual Content → Entity Extraction
Factual statements are written as event-like JSON files alongside regular event files:
`output/{BookName}/{chapter}-notes-event.json`

Structurally identical to normal event files so existing entity extractors work unchanged.
One file per chapter, aggregating all factual notes from that chapter's endnotes/footnotes.

Each sub-event carries a `source_reference` for provenance:

```json
{
  "Sub-eventID": "01XXXX...",
  "Sub-event_summary": "DSC awards for actions on 10 July",
  "Sub-event_fulltext": { "paragraph_1": "Capt. Harry L. Gentry..." },
  "source_reference": {
    "reference_type": "endnote",
    "reference_number": "18",
    "source_EventID": "01KKWTB0C1...",
    "source_Sub-eventID": "01KKWTB0C2...",
    "BibliographyID": "01ZZZZ..."
  }
}
```

- `source_reference.BibliographyID` links to the bibliography file when the original
  note was a mixed entry that got split (factual + citation). `null` when the note
  was purely factual with no document citation.
- `source_EventID` / `source_Sub-eventID` link back to the original event/sub-event
  that contained the endnote/footnote reference.
- Entity extractors ignore `source_reference` — it's provenance metadata only.

### Ambiguous → Human Review
Written to `output/bibliography/review_queue.json`:
```json
[
  {
    "book": "BreakoutAndPursuit",
    "chapter": "chapter7b",
    "reference_number": "18",
    "verbatim_reference": "...",
    "EventID": "...",
    "Sub-eventID": "..."
  }
]
```

### Pipeline Flow
```
extract_supplemental()
  ├── Grok classifies each note as document_reference / factual_content / ambiguous
  ├── document_reference → merge_or_create in output/bibliography/
  ├── factual_content → write {chapter}-notes-event.json → entity extractors pick up
  └── ambiguous → append to review_queue.json
```

### Migration
- `output/supplemental/` is replaced entirely
- Supplemental cache cleared for re-extraction
- `_extract_supplemental` in phase2_extract.py updated to call new flow

## Files Changed
- `src/extraction/supplemental.py` — Major rewrite of classification + output routing
- `phase2_extract.py` — Update `_extract_supplemental` to handle new output dirs
- `config.yaml` — No change (still `supplemental_material.enabled`)

## Not Changed
- `src/extraction/supplemental_advanced.py` — Check if still needed
- Entity extractors — No changes; they already process event files
