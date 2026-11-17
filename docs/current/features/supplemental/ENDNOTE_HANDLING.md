# Endnote / Footnote Handling

How endnote and footnote text gets from the source into the supplemental and
bibliography records, and the source-format rules it depends on.

Replaces `messages/TODO_fetch_endnote_content.md` (2026-03-17). That TODO's four
fix steps are implemented; one part of its first problem is still open (see
[Open issue](#open-issue)).

## Why this exists

The supplemental prompt used to receive only reference **numbers** (`[4, 5, 6]`),
not the reference text. With nothing to extract from, Grok invented citations:
one run produced the same Zaloga reference 247 times in `verbatim_reference`. The
fix is to give the model the real text, so it classifies and parses a citation
instead of inventing one.

## Source format (ibiblio / HyperWar green books)

- In-text markers link to a per-chapter notes page, e.g.
  `https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn1.html#fn8`.
- One notes page holds every note for the chapter, so one fetch covers the chapter.
- Breakout and Pursuit uses end-of-chapter endnotes; Cross-Channel Attack is
  footnote-driven. Both use numbered references.
- Notes can point at other notes, e.g. *"10. See, for example, Ltr, Eisenhower to
  Bradley, 25 Jun, cited in n. 5, above."* Note 10 then carries note 5's sources.
- A single note can cite several distinct works, separated by **semicolons**:

  > 5. Ltr, General Dwight D. Eisenhower to Lt Gen Omar N. Bradley, 25 Jun, FUSA G-3
  > Jnl File; Dwight D. Eisenhower, Crusade in Europe (New York: Doubleday & Company,
  > Inc., 1948), pp 245, 265; Answers by Lt Gen Walter B. Smith and Maj Gen Harold R.
  > Bull to questions by members of the Hist Sec. ETOUSA, 14-15 Sep 45, OCMH Files.

  That is three works: a letter (primary document), a book, and a government
  document. Commas occur *inside* a citation, so in this corpus only the semicolon
  separates works.

## Pipeline

| Step | Where | What happens |
|---|---|---|
| 1. Capture text at parse time | `src/parser.extract_footnotes` | Pairs each in-text `#fn` marker with its `#cn` definition and stores `{number, url, text}` in the `footnotes` array of `*-parsed.json`. |
| 2. Save per-chapter endnote refs | `phase1_parse._save_endnote_refs` | Writes `chapter<N><section>-endnotes.json` with `number`, `url`, `text`. Logs `OBSERV phase1.endnotes_dropped` on a failed write. |
| 3. Get the text for a chapter | `src/extraction/fetch_endnotes.fetch_endnote_texts` | Uses parser-captured `text` first (`_partition_footnotes`). Fetches the HTML notes page only for numbers with no text (older parses), grouping by page so each page is fetched once. |
| 4. Resolve cross-references | `fetch_endnotes._resolve_cross_references` | Rewrites "cited in n. 5", "cited n. 5" and "see n. 5" in place as `[see n. 5: <text of note 5>]`, so the model sees the inherited sources. Runs before any prompt is built. |
| 5. Put the text in the prompt | `supplemental._window_prompts` -> `_sub_event_prompt` -> `_build_ref_context` -> `fetch_endnotes.format_endnote_text_block` | Asks per 3-paragraph window of each sub-event's raw paragraphs (`windows.sub_event_windows`); a note is asked once per sub-event, in the first window whose paragraphs carry its marker (`_window_refs`). Adds an "Actual endnote/footnote text (from source document)" block listing each referenced note's text. |
| 6. Split multi-work notes | `bibliography._expand_multidoc_notes` (deterministic, from `_store_notes` / `extract_bibliography_deterministic`) | Splits a note on `;` into one sub-note per work. Sub-notes are numbered `5`, `5b`, `5c` so each becomes a separate mention. The supplemental prompt also tells the model to split semicolon/period-delimited sources. |

Back-references inside the bibliography (`Ibid.`, `op. cit.`, short-title repeats)
are resolved separately by `bibliography._resolve_back_reference`, and bare stubs
(under 4 characters, or `Ibid.`-style with no antecedent) are skipped by
`bibliography._is_stub_citation`.

## Rules to keep

- **Source text wins.** Never ask the model for citation text the source already
  has. Fetch or parse it and pass it in.
- **Delimiter is corpus-specific.** `_expand_multidoc_notes` takes a `delimiter`
  argument. Semicolon is right for the green books; do not assume it (or comma) for
  other corpora.
- **Resolve cross-references before prompting.** The model must see note 5's text
  when reading note 10.
- **Prefer parse-time capture over re-fetching.** The HTML re-fetch needs network
  access (NAT) and is exposed to link rot; it is a fallback for older parses only.
  It goes through the shared SSRF-guarded fetch (see the SSRF item in `TODO.md`).

## Locators are deterministic

The prompt no longer asks for `reference_number` or `verbatim_reference`. After each answer,
`supplemental.locate_notes` matches every returned item to the note it came from (`_note_of`) and
sets `reference_number`, `reference_type` and `verbatim_reference` from the note itself
(`_put_locator`, `_verbatim`: the note's own words for a citation that names its document; empty
for prose items, `Ibid.` stubs, or notes with no text). `_observe_note_coverage` reports notes
that no window asked. Items it cannot match are counted as not located (provenance check P2-Q2).

## Open issue

**`reference_type` (endnote vs footnote) can still come from the LLM.** `_put_locator` replaces
the model's `reference_type` with the asked note's kind only when it is empty or `bibliography`;
an `endnote`/`footnote` value the model returns is kept. `_build_ref_context` adds an
endnote/footnote hint to the prompt only when no note text was found. The type should come from
the source structure (marker style and where the definition lives) in every case.

## Tests

- `tests/test_fetch_endnotes.py`: HTML notes parsing.
- `tests/unit/test_footnote_citation_text.py`: parser-captured text is preferred over
  the HTML fetch; malformed entries are skipped.
- `tests/unit/test_bibliography_deterministic.py`: splits on semicolon, not comma;
  stub detection.
- `tests/test_phase1_split_endnotes_and_hash.py`: split chapters still write endnote refs.
