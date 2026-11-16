# Language Detection & Translation (Phase 0)

**Status:** **implemented, ON by default** (owner 2026-11-16; per-page translate-at-the-seam;
`prompts/translation.yaml`, `prompts/language_detect.yaml`, `src/ingestion/translation.py`; tests
`tests/test_translation.py` + `tests/test_translation_wiring.py`). `translation_enabled` is true
unless `ingestion.translate: false` in `config.yaml` or `PHASE0_TRANSLATE=0`. English costs nothing:
`clearly_english` decides it deterministically (Latin script; English function words present, none of
German / French / Italian / Spanish / Dutch common; markup removed) and only a page or section that is
not clearly English asks Grok its language (Cross-Channel Attack, 562 pages: 523 decided free, 39 -
indexes, rosters, map pages - asked).
**Owner decision:** **per-page** detection (revised from per-document after the
M1019 microfilm example); convert original → markdown, then translate non-English
pages to English via Grok; every translated page is marked (inert) as translated
from its language; all established Phase 1/2/3 practices run on the English
markdown unchanged.

## Problem

The corpus has been English-only, but WWII sources will increasingly be in other
languages — **German, Russian, French, Italian, Chinese, Japanese** most
probably. The entire extraction/dedup/enrichment stack (Phase 1–3) is
English-centric: prompts assume English, and entity name-matching
(`text_utils.normalize_name_ascii`) transliterates **Latin/European** scripts to
ASCII and cannot meaningfully resolve Cyrillic or CJK.

## Decision (Strategy B: translate-to-English at ingest)

Normalize language **at Phase 0**, so Phase 1+ never sees a non-English
document:

1. **Detect** the document's language (per **document**, not per page/mention).
2. **Convert** the original source → Markdown *in its original language* (the
   existing Phase 0 converters: Chandra OCR, `pdf_pipeline`, `text_converters`).
   This is the faithful capture.
3. **Translate** that Markdown → **English Markdown** via **Grok**, instructing
   it to render proper nouns (people, places, units) in their **conventional
   English forms** (München→Munich, Гудериан→Guderian, 東京→Tokyo).
4. The **English markdown feeds Phase 1**; the **original-language text is
   kept** for provenance and re-translation (where, per track: see "Storage layout").

Rejected: Strategy A (extract in original language) would push cross-script
entity resolution onto the dedup layer, which today cannot do it. Strategy C
(full multilingual prompts + canonical/surface-form entity model) is a project,
not warranted for a primarily-English corpus. **B protects the existing
English-centric investment** and — critically — because proper nouns are
normalized to conventional English during translation, the existing Latin-script
name-matching keeps working (everything is English/Latin by Phase 2).

## Why per-page (revised from per-document)

The initial decision was per-document, assuming "a document = one coherent
language." The **M1019 microfilm** example disproved that for scanned rolls: a
roll is a *container of many small documents* in mixed languages — an Allied
interrogation is an **English cover/summary + a German transcript**, page by
page. Per-document detection would mis-handle the minority-language pages
(detect the dominant language and translate everything as if it were that).

So the detection/translation unit is the **physical page** (the pages Chandra
emits with `--paginate_output`, split via `chunk_pages.split_pages`):

- Each page is detected independently.
- English pages pass through untouched (no Grok call).
- Non-English pages are translated and **prefixed with an inert
  translation-provenance marker** (below).
- Pages are reassembled in order into the English `.md`.

This handles mixed-language rolls correctly and reassembles them into one
normalized-English document. Detection is per-page (not batched); a clearly English
page needs no detect call at all, and the remaining detect calls are small and cached —
the translations (foreign pages only) are the real cost, consistent with "LLM once per
item at ingest."

**Documents without pages** (converted EPUB/DOCX/HTML: `normalize_to_english`) are mixed-language
too: they are detected per SECTION of at most 4,000 characters (`_language_runs`, on Markdown
block boundaries), neighbouring sections of one language merged, and only the non-English runs
translated - each with its own marker. (Before 2026-11-16 one language was taken for the whole
document from its first 4,000 characters, so a German part after an English opening was never
translated.)

**Known limit:** the unit is a page / section. A short quotation in another language inside a
mostly-English page (a German order quoted in an English narrative) is detected as the page's
primary language and stays in the original.

## Translation-provenance marker (inert)

Every **translated page** is prefixed with an HTML comment:

```
<!-- translated from German by grok:grok-4.6 -->
```

It is **inert to `src/parser.py` and entity extraction** (HTML comments are
ignored — same mechanism as Chandra's `<!-- Page N -->` markers), so provenance
travels *with the text* without contaminating extracted entities. English
(pass-through) pages get no marker. Belt-and-suspenders: the `.lang.json`
sidecar records per-page languages too, and the marker can be parsed back out
later if required.

## Storage layout

**As built, per track (2026-11-16).** Translation is called through one adapter,
`translation.normalize_markdown_to_english(markdown, per_page=...)` (never raises, returns the input on
disable / English / error):

| Track | Where it runs | Per-page? | Original kept as |
|---|---|---|---|
| OCR narrative | `ocr_promote.promote(translate=...)`, called by `ocr_merge_handler._publish` and the `phase0_ocr_crosscheck.py` task, after sanitising | yes | `ocr-output/<slug>/merged/raw.md` (the merged OCR exactly as Chandra wrote it; the bucket keeps every version) |
| CONVERT (epub/docx/html) | `phase0_convert.convert_key` | no (whole document) | the uploaded source file |
| VIDEO | `phase0_video._translate_segments` | per segment | transcript timecodes and speaker labels are not sent to Grok |
| OOB reference | `phase0_ingest._translate_converted_markdown` | yes | `<stem>.orig.md` + `<stem>.lang.json` sidecar |

Only the OOB track writes the `.orig.md` / `.lang.json` / `.meta.yaml` layout below; the other
tracks keep no per-page language sidecar, and chapter meta does not carry a `source_language` field.
The layout below is the original design, still accurate for the OOB track:

```
<source>/
  <name>.orig.md        # original-language markdown (faithful capture)   [kept]
  <name>.md             # English markdown — feeds Phase 1 (pass-through if src English)
  <name>.meta.yaml      # + source_language, translated, translator fields
```

- If the detected language **is English**, **no translation runs, no Grok call,
  and no `.orig.md` is written** — the single `.md` is both the original and the
  pipeline input (the common, zero-cost case). `translated: false`.
- The English `.md` is the **only** artifact Phase 1 discovers/ingests; the
  `.orig.md` (present only for translated docs) is inert to the pipeline
  (provenance only).

## Metadata stamping

The mention schemas already carry `source_language` (today hard-coded
`"English"` in prompt examples). Phase 0 writes a per-file `<stem>.lang.json`
sidecar capturing **per-page** provenance:

- `source_languages`: distinct languages seen across pages (e.g.
  `["English", "German"]` for a mixed roll).
- `translated`: `true` when any page was translated.
- `translator`: `"grok:<model>"` (the bibliography schema already has a
  `translator` field precedent).
- `pages`: `[{page, source_language, translated}, …]` — the per-page detail.

## Translation mechanics

- **Engine:** Grok via `GrokClient.chat_completion` — no new dependency, and it
  already handles the markdown. Prompt externalized to `prompts/translation.yaml`.
- **Chunking:** (`translation.split_markdown_chunks`) long documents are translated in chunks (same discipline as OCR
  auto-split / extraction), preserving markdown structure (headings, tables,
  image refs) across chunk boundaries. One LLM pass per document at ingest —
  consistent with the "LLM once per item at ingest, never per visit" cost
  principle.
- **Proper-noun instruction:** the prompt directs conventional-English rendering
  of people/place/unit names and to **preserve markdown/table/image markup
  verbatim** (translate cell *text*, not structure).
- **Caching:** translation responses cached like other Grok calls, so re-runs of
  unchanged documents don't re-pay.

## Known limitations (accepted for v1)

- **Translated verbatim quotes are not the original.** For a citable corpus, a
  translated quotation differs from source wording. Mitigation: the
  original-language markdown is retained, so provenance can point at the
  original even though extraction runs on the English text. Full
  original-quote-preservation is a later enhancement.
- **Transliteration consistency** of proper nouns depends on Grok
  (Гудериан→"Guderian" not "Gudarian"). Lower risk than full cross-script
  resolution, but worth spot-checking on the first non-English sources.
- **CJK** (Chinese/Japanese) OCR quality via Chandra is untested on this corpus;
  detection+translation is unaffected, but capture fidelity should be validated
  when the first CJK source arrives.

## Scope boundary

Phase 1/2/3 are **unchanged**. This is purely a Phase 0 normalization step. The
only schema touch is *populating* the existing `source_language` field with real
detection rather than a hard-coded default.

---

## Related documents
- [Award Sourcing](AWARD_SOURCING.md) — reuses this translate-at-the-seam pattern to render non-English award citations in English (original + language preserved).
- [Ingestion Front-End](INGESTION_FRONT_END.md) — Phase-0 ingestion where source-language detection + translation occur.
- [People Biographical Enrichment](../features/people/biographical-enrichment.md) — consumes translated citations.
