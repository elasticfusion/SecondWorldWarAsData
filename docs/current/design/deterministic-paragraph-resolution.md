# Design: Deterministic paragraph resolution (stop the LLM transcribing identifiers we own)

**Status:** DESIGN + PROTOTYPE (2026-10-30). Owner-raised. Provenance is the #1 project goal.

## Problem

Events extraction asks Grok to return, per sub-event, a `paragraph_numbers: [int, ...]` array —
the paragraph numbers belonging to that sub-event. The code (`events._reconstruct_fulltext`) then
looks those numbers up in `{absolute_number -> text}` to build `Sub-event_fulltext`, which every
downstream entity extractor (people/places/dates/groups/...) reads.

**This makes the LLM responsible for transcribing identifiers the pipeline already owns.** The
paragraphs are fed to the model already numbered (`Paragraph_1 .. Paragraph_N`); asking it to copy
those integers back is a mechanical task LLMs do unreliably. Observed failure (XChannel-8 2026-10-30):
a fresh events call emitted sequential `paragraph_numbers` **264..1700** against a **263-paragraph**
document — pure hallucination. Result: `no_resolved_paragraphs` → `Sub-event_fulltext` degrades to
summary-only → **every entity extractor starves** (people=0, places=0) → `yield.fail_stop`. It is the
recurring "paragraph_numbers" fingerprint.

This **violates a rule the codebase already follows elsewhere**: entity identifiers (`PersonID`,
`GroupID`, `PlaceID`) are **minted server-side** (`ulid.new()`) and the LLM's value is never trusted
(`_mint_person_ids`, `GroupID = str(new_ulid())`). Paragraph numbers become a **provenance key**
(sub-event → paragraph → source section), so they deserve the same rule: **the LLM must never own an
identifier that becomes a provenance link.**

## Principle

> The LLM does COMPREHENSION (what text forms a coherent sub-event). The CODE does IDENTITY
> (which paragraph `absolute_number`s that text maps to). The LLM never emits a provenance key.

## Design

Change the events contract so the model returns, per sub-event, **anchor quotes** — short verbatim
snippets copied from the source — instead of paragraph numbers:

```jsonc
{
  "Sub-eventID": "01ULID...",
  "Sub-event_summary": "...",
  "anchor_quotes": [
    "Force U (seaborne units of the VII Corps) was marshaled in the Tor Bay area",
    "the entire 5th Armored Division"
  ],
  "references": []
}
```

The code deterministically resolves `anchor_quotes -> absolute_number[]` by matching each quote
against the parsed paragraph text, then builds `Sub-event_fulltext` exactly as today. The model
still does the hard part (grouping), but a *quote* is self-validating (it either appears in the
source or it doesn't) in a way a *number* is not.

### Resolver (`resolve_anchor_paragraphs`)

For each anchor quote:
1. **Normalize** both the quote and each paragraph's text (collapse whitespace, casefold, strip
   punctuation runs) so minor model reformatting still matches.
2. **Exact-substring** match first: the normalized quote is contained in a normalized paragraph →
   that paragraph's `absolute_number`.
3. **Fallback fuzzy** (only if exact fails): longest-common-substring / token-overlap ratio above a
   threshold → best-matching paragraph; ties or below-threshold → unresolved (do NOT guess).
4. A quote that resolves to a paragraph **not adjacent** to the sub-event's other resolved
   paragraphs is kept (sub-events can span); an UNRESOLVED quote is recorded loudly
   (`OBSERV events.anchor_unresolved`) but does not fabricate a number.
5. The sub-event's paragraph set = the union of resolved `absolute_number`s (dedup, sorted).

### Why this is robustly better

- **Self-validating:** a hallucinated quote simply won't substring-match → it resolves to nothing
  and is flagged, rather than silently pointing at a wrong-but-valid paragraph (the number case can
  mis-attribute to a real paragraph — undetectable corruption). A quote can only resolve to text
  that actually contains it.
- **Deterministic identity:** the `absolute_number` is assigned by `code`, never transcribed by the
  model → eliminates the entire out-of-range hallucination class (not just detects it like the
  interim #642 guard).
- **Provenance-exact:** the resolved paragraph is, by construction, one that literally contains the
  quoted source text — the strongest possible record→paragraph link.

### Migration / compatibility

- Keep `_reconstruct_fulltext`'s number→text path for cached/old responses; add the
  anchor-quote→number resolution in front of it (if `anchor_quotes` present, resolve → set
  `paragraph_numbers`, then the existing path builds fulltext unchanged).
- The interim #642 `_validate_paragraph_range` guard stays as defense-in-depth for any residual
  number-based responses.
- Prompt change: ask for `anchor_quotes` (verbatim short snippets) instead of `paragraph_numbers`.
- Yield/provenance gates unchanged downstream (they read `Sub-event_fulltext`).

### Risks / open questions

- **Quote fidelity:** the model must copy *verbatim* (not paraphrase). Normalization absorbs
  whitespace/case/minor punctuation; a paraphrased "quote" falls to the fuzzy fallback or
  unresolved. Mitigation: prompt instructs "copy EXACT text, 6–15 words."
- **Duplicate text:** a quote appearing in multiple paragraphs → resolve to all matches (a sub-event
  spanning repeats) or the first; prototype resolves to the first exact match + logs multiplicity.
- **Cost/latency:** anchor quotes are a few words × sub-events — comparable to / smaller than the
  current number arrays; resolution is O(quotes × paragraphs) substring, trivially fast.
- **Performance:** for very long chapters, index paragraphs by normalized text for O(1) exact lookup
  (prototype builds a normalized index).

## Prototype

`src/extraction/anchor_resolve.py` (+ `tests/test_anchor_resolve.py`): `resolve_anchor_paragraphs`
implementing normalize + exact-index + fuzzy-fallback + unresolved reporting, independent of the
events flow so it can be unit-tested and then wired into `_reconstruct_fulltext`.
