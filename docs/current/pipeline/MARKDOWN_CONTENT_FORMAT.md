# Markdown content format (what Phase 1 reads)

Status: implemented. This is the one description of the chapter markdown that Phase 1 parses, whether
it was written by hand or produced by a Phase-0 converter (PDF/OCR, EPUB, DOCX, HyperWar HTML, paper).
Other docs link here instead of repeating the syntax.

Code: `src/discovery.py` (layout), `src/parser.py` (metadata, paragraphs, pages, images, maps),
`src/notes.py` (notes and site navigation), `src/ingestion/translation.py` (translation marker),
`src/ingestion/book_slug.py` (slug), `src/ingestion/chapter_meta.py` (meta writer),
`phase1_parse.py` (driver).

## 1. Folder and file layout

```
contentrepository/[<source>/]<slug>/chapterN/chapterN-content.md
contentrepository/[<source>/]<slug>/chapterN/chapterN-meta.yaml
```

- `<slug>` is the book. `<source>` is optional: `_system`, or a segment starting `user-`, `contrib-` or
  `org-`. `content_slug(key)` skips a leading source segment and returns the next folder
  (`src/ingestion/book_slug.py:58-69,88`). A loose file under `contentrepository/` is named by its
  filename stem. The slug must match `[A-Za-z0-9._-]+` (spaces in a folder name become `_`); anything
  else is `unknown` and is rejected, never processed.
- The slug is immutable once assigned (see `architecture-decisions`, "Book identity"). A source
  document (PDF, EPUB) is named by its own filename stem (`book_slug`), and Phase 0 writes its chapters
  to `contentrepository/<stem>/chapterN/`, so both rules give the same slug.
- Phase 1 scans one content root whose children are the book folders (`discover_content_structure`, `src/discovery.py`). The book-scoped ECS run syncs `contentrepository/<book>/` down
  (`ecs_entrypoint.py:1359`).
- Folders named `sourcedocument` and `_figures`, and dot-folders, are skipped.
- A chapter folder must be named `chapter<digits>` or `chapter<ROMAN>` (`chapterIV`; Roman numerals
  are converted by `_convert_chapter_number` in `src/discovery.py`). Other folders are skipped with
  `OBSERV discovery.chapter_skipped reason=no_chapter_num`.
- A chapter folder with no meta file is skipped (`reason=no_meta`); with no content file it is skipped
  (`reason=no_content`). **The meta file is therefore required.**
- Content files: either one `chapterN-content.md`, or sections `chapterNa-content.md`,
  `chapterNb-content.md`, ... (any trailing letters; read in alphabetical order, paragraph numbering
  continues across them). Sections win: if any `chapterN<letters>-content.md` exists the plain file is
  ignored (`_find_content_files`, `src/discovery.py`).
- Fallback: a book folder with no chapter folder but a flat `<slug>/*.md` is parsed as a single chapter 1
  (prefers `*-content.md`; the meta file is optional there).

## 2. The meta file

`chapterN-meta.yaml` is found by `*-meta.yaml`, else the legacy `*-meta.md` (`_find_meta_file`). It is
read by `parse_metadata` (`src/parser.py:84-93`) and written by Phase 0 with `build_meta_yaml`
(`src/ingestion/chapter_meta.py:20`).

| Key | Default if missing | Notes |
|---|---|---|
| `book` | `""` | Must equal the folder slug. If not, Phase 1 raises the `phase1.book_slug_mismatch` error alert and uses the folder slug (`phase1_parse.py:370`). Set it. |
| `chapter_title` | `""` | Stored on every parsed paragraph's document. |
| `series` | `""` | |
| `author` | `""` | |
| `license` | `Public Domain` | Defaults to Public Domain when the key is absent. Set it for anything else. `build_meta_yaml` writes `""`. |
| `copyright_date` | `""` | |
| `source_url` | `""` | Base for resolving relative image and map links and for note URLs. Use a real `http(s)://` URL or leave it empty. |

Other keys (`chapter_number`, `book_id`, `source_url_basis`, `source_key`) are written by converters but
are not read by `parse_metadata`. The chapter number comes from the folder name (discovery) and the
`chapterN` in each content file name (`src/parser.py:1497`). A YAML file that is not a mapping
yields empty metadata with `OBSERV parser.meta_not_mapping`.

The legacy `-meta.md` form is positional (one non-blank line each): series, (skipped), book, author,
`<n> - chapter title`, license (`_parse_legacy_metadata`). Use YAML for new content.

## 3. Paragraphs, headings, quotes, lists, tables

Phase 1 splits the body (after notes and page markers are removed) on **blank lines**. Each block is one
paragraph, numbered from 1 across the chapter. Line breaks inside a block are kept in the text.

- **Headings.** A block that is only a heading whose text is wrapped in asterisks (`## *Preparations*`)
  is dropped, because the title is held in the meta file. A plain heading (`# The Landing`,
  `## Preparations`) is NOT dropped: it becomes a paragraph (`_HEADING_PATTERN`,
  `src/parser.py:27,502`). Notes section headings are a separate case (section 5).
- **Block quotes.** A block whose every non-empty line starts with `>` is a quote: `is_quote=true`, the
  `>` markers are removed from `text`. Quote marks inside prose do not make a quote. Quote a multi-line
  passage by putting `>` on every line.
- **Structure repair (opt-in).** With `ingestion.repair_markdown_structure: true` in `config.yaml` or
  `PHASE1_REPAIR_STRUCTURE=1`, Phase 1 first runs `markdown_structure.detect_block_quotes` on the file
  and adds `>` to OCR quotations it recognises (a lead-in paragraph plus quoted paragraphs); it never
  changes the words and records the attribution as `quote_attribution`. Flattened task-organisation
  tables are flagged in `table_hints`, not rewritten. Off by default (`phase1_parse.py:500`), so
  hand-written content needs explicit `>` marking.
- **Lists and tables.** There is no list or table parser. A bullet list or a pipe table is one paragraph
  (one blank-line block) whose text keeps its markdown. Put a blank line between a list or table and the
  prose around it, and between two lists you want kept apart.
- **Separators.** A line `* * *` alone between blank lines is removed and acts as a block break.
- **HTML comments** are not removed by the parser, except the translation marker (section 7).

## 4. Page markers

A marker gives the page that starts at that point; a paragraph takes the page of the last marker at or
before its start (`_page_in_effect`, `src/parser.py:599-607`). Two forms (`extract_page_markers`,
`src/parser.py:96-111`):

- `<a id="page11"></a>` (also `<span id="page11"></span>`, what pandoc writes for HyperWar anchors).
  Recorded with `marker_type: anchor`.
- `*\--12--*` (the OCR page separator; the text is an asterisk, backslash, two hyphens, the number, two
  hyphens, an asterisk). Recorded as `separator`.

Markers are removed from the paragraph text. A paragraph before the first marker has `page_number: null`.
A paragraph that runs across a marker gets `page_end`. The page is found by searching the paragraph's
first 60 characters in the body. A block-quote whose first line is shorter than 60 characters is not
found (the `>` on its second line is not in the paragraph text), so it takes the last known page and logs
`OBSERV parser.page_unlocated`. Keep the first line of a quote longer than 60 characters, or accept the
warning.

## 5. Notes: markers and the notes section

`separate_notes_detailed` (`src/notes.py:246-265`) cuts the notes section out of the body, so notes are
never paragraphs, and rewrites in-text markers.

**In-text markers** become `<sup>[N](<source_url>#fnN)</sup>` (just `<sup>[N]</sup>` when `source_url` is not
an http(s) URL). Accepted input forms:

- `<sup><a href="#fnN" id="cnN">N</a></sup>` (quotes optional, extra attributes allowed)
- `<sup>N</sup>` (a bare number). At the start of a line it is treated as a note definition left in the
  body and is not rewritten.
- `<sup>[N](https://host/page.html#fnN)</sup>` (already canonical)

`[^1]` is NOT supported: it stays in the text, no note is created, and a `[^2]: text` line is appended
to the preceding note's text.

**Notes section.** Starts at a heading line `Footnotes`, `Endnotes` or `Notes` (any `#` level, case
insensitive, optional `**bold**`, optional `>` quoting). When there are several such headings, the last
one whose section contains at least one definition is used. The heading word sets the note `kind`:
`Footnotes` gives `footnote`, `Notes` and `Endnotes` give `endnote`. The section ends at the first
unquoted horizontal rule (`-----...` of 10 or more, `***`, `___`) or a line starting
`Transcribed and formatted`, or at the end of the file.

**Definition lines** (a leading `>` is ignored):

- HTML form: `\[<a href="#cnN" id="fnN">N</a>\] note text` (the backslashes are optional; the link
  must point to `#cnN`).
- Markdown form: `[N](https://host/page.html#cnN). note text`.

A note runs to the next definition, across blank lines (paragraphs are joined with a newline). It also
stops at a heading or navigation line. Pandoc backslash escapes in the text are removed. If a number is
defined twice the first definition is kept and `parser.notes_duplicate_definition` is warned.

Each note records `number`, `kind`, `text`, `url` (`<source_url>#fnN`, or the page URL carried by a
markdown definition) and `paragraph_number`, the first paragraph holding its canonical marker. If a
file has no recognisable notes section, `extract_footnotes` (`src/parser.py:408-463`) is the fallback
for the canonical `<sup>[N](url)</sup>` markers plus `[N](url#cnN). text` definitions. A chapter's notes
may sit in its last section file; earlier sections' markers are linked chapter-wide
(`_link_chapter_notes`).

**Site navigation.** A line holding two or more links among `[Table of Contents](...)`,
`[Previous Chapter](...)`, `[Next Chapter](...)` is removed from the body (`_strip_nav`,
`src/notes.py:209-219`). A single such link in prose is kept.

## 6. Images and maps

Images (`extract_images`, `src/parser.py:135-167`) are read from the whole file, including notes. Forms:

| Markdown | Kind |
|---|---|
| `![alt](https://host/x.jpg)` | external |
| `![alt](icn/x.jpg)` | relative; needs `source_url`, and an image extension (`jpg jpeg png gif tif tiff webp`); resolved against `source_url` |
| `![alt](:/resourceid)` | embedded (Joplin resource) |
| `[![alt](:/id)](https://url)` | combined Joplin and external |
| `[![alt](https://img)](link)` | linked thumbnail; the inner image URL is used |
| `<a href="maps/x.jpg">...</a>` | raw-HTML anchor to an image file (pandoc passthrough) |

An image is anchored to the paragraph that holds it (or the paragraph citing a note that holds it).
A caption is paired from an adjacent caption line. An image whose URL is over 2000 characters is dropped
with `OBSERV parser.image_dropped`. A file is scanned only up to its first 500,000 characters
(`parser.text_truncated`).

Maps (`extract_maps`, `src/parser.py:319-357`): `[Map X](target)`, where the label text starts with
`Map` (so `map_id` is `X`), and raw-HTML anchors whose link text starts with `Map` or whose href is under
`/maps/`. `target` is absolute, or relative (`maps/B-I.jpg`) when `source_url` is set and it looks like an
image or a `maps/` path; other relative links are ignored. Each map records the paragraphs that mention
"Map X" (`references`); a map with none logs `OBSERV maps.ref_gap` (a provenance warning).

## 7. Translation marker (Phase 0)

When Phase 0 translates a non-English page it appends `<!-- translated from <Language> by <model> -->`
to the end of the FIRST line of each translated paragraph (`mark_translated`,
`src/ingestion/translation.py:561`; marker regex line 505). Phase 1 removes the markers before
parsing (`lift_translation_markers`), aligns the marked lines to the resulting paragraphs and notes, and
sets `translated_from` (for example `French`; several languages are comma-separated) on each paragraph
or note holding such a line. A marked line that lands in no paragraph logs
`parser.translated_line_unplaced`. Hand-written content does not need the marker unless it is a
translation; if it is, add one marker per translated paragraph exactly in this form (`by` takes no
spaces).

## 8. Complete minimal example

`contentrepository/B/chapter1/chapter1-meta.yaml`:

```yaml
series: Example Series
book: B
author: A. Author
chapter_number: "1"
chapter_title: The Landing
license: Public Domain
copyright_date: "1951"
source_url: https://example.org/docs/B/ch1.html
```

`contentrepository/B/chapter1/chapter1-content.md`:

```markdown
# The Landing

## *Preparations*

<a id="page11"></a>The division embarked on 5 June.<sup><a href="#fn1" id="cn1">1</a></sup> The weather was poor, as shown on [Map I](maps/B-I.jpg).

The second paragraph runs
over two lines.

> The general wrote that the division would go ashore at dawn on the sixth of June, without delay.
> He added nothing more.

- first item
- second item

| Unit | Strength |
|------|----------|
| 1st Div | 14000 |

*\--12--*

La division embarquait le 5 juin. <!-- translated from French by grok-4 -->
Suite de la phrase.

![Landing craft](icn/B-1.jpg)

### Footnotes

\[<a href="#cn1" id="fn1">1</a>\] 1st Div G-3 Jnl, 5 Jun.
```

Verified with `parse_content_file` (section `""`, start paragraph 1). Result:

| # | page | `is_quote` | `translated_from` | text starts |
|---|---|---|---|---|
| 1 | null | no | none | `# The Landing` (plain heading kept; `## *Preparations*` dropped) |
| 2 | 11 | no | none | `The division embarked on 5 June.<sup>[1](https://example.org/docs/B/ch1.html#fn1)</sup> ...` |
| 3 | 11 | no | none | `The second paragraph runs\nover two lines.` |
| 4 | 11 | yes | none | `The general wrote that ...` (`>` removed) |
| 5 | 11 | no | none | `- first item\n- second item` |
| 6 | 11 | no | none | the three table lines |
| 7 | 12 | no | French | `La division embarquait le 5 juin.\nSuite de la phrase.` (marker removed) |
| 8 | 12 | no | none | `![Landing craft](icn/B-1.jpg)` |

- Footnotes: one, `number=1 kind=footnote url=https://example.org/docs/B/ch1.html#fn1 paragraph_number=2
  text="1st Div G-3 Jnl, 5 Jun."`. The notes section is not a paragraph.
- Page markers: `(11, paragraph 2, anchor)` and `(12, paragraph 7, separator)`.
- Maps: one, `map_id=I`, `url=https://example.org/docs/B/maps/B-I.jpg`, `paragraph_number=2`.
- Images: one, external, `https://example.org/docs/B/icn/B-1.jpg`, anchored to paragraph 8.

Other forms checked the same way: `<span id="page5"></span>`, a bare `<sup>1</sup>` marker, a two-link
navigation line (removed), a `## Endnotes` section with `[N](https://h.org/p.html#cnN). text` definitions
(kind `endnote`, a continuation line joined into the note text), and `[^2]` (not parsed, see section 5).

## 9. Writing checklist

1. Folder is the immutable slug; `meta.book` equals it.
2. One meta file per chapter folder, one or more content files.
3. Blank line between blocks; `>` on every line of a quote; plain headings become paragraphs.
4. Page markers at page starts, if you want page citations.
5. Notes: `<sup>` markers in text, a `Footnotes`/`Endnotes`/`Notes` heading, one definition per note.
6. Set `source_url` if images, maps or note links are relative.
