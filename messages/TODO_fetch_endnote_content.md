# TODO: Supplemental Extraction — Fetch Endnote/Footnote Content

**Priority:** High  
**Created:** 2026-03-17

## Problems

### 1. Endnote/footnote type misclassification
Grok decides `reference_type` ("endnote" vs "footnote") with no source data. BreakoutAndPursuit is endnote-only but output contains `*-footnotes.json` files. CrossChannelAttack is footnote-driven. The ibiblio sources use numbered end-of-chapter references for both books — the type should come from the source HTML structure, not Grok's guess.

### 2. Endnote/footnote text is never fetched
Parsed files contain footnote URLs:
```json
{"number": 8, "url": "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn1.html#fn8"}
```

The actual reference text lives at that URL, e.g.:
> "See, for example, SHAEF to AGWAR, S-54425, 23 Jun, SHAEF Msg File."

But the supplemental prompt only receives reference **numbers** (`[4, 5, 6]`), not the text. Grok has nothing to extract from, so it hallucinates.

### 3. Actual reference content not in output JSON
Because the text is never fetched, the `verbatim_reference` field in the output JSON contains fabricated content (e.g., Zaloga reference repeated 247 times) instead of the real reference text from ibiblio.

### 4. Cross-references between endnotes not resolved
Endnotes can reference other endnotes, e.g.:
> 10. See, for example, Ltr, Eisenhower to Bradley, 25 Jun, cited in n. 5, above.

This means endnote 10 inherits the sources from endnote 5:
> 5. Ltr, General Dwight D. Eisenhower to Lt Gen Omar N. Bradley, 25 Jun, FUSA G-3 Jnl File; Dwight D. Eisenhower, Crusade in Europe (New York: Doubleday & Company, Inc., 1948), pp 245, 265; Answers by Lt Gen Walter B. Smith and Maj Gen Harold R. Bull to questions by members of the Hist Sec. ETOUSA, 14-15 Sep 45, OCMH Files.

Note: endnote 5 itself contains multiple distinct sources separated by semicolons:
1. A letter (primary source document): "Ltr, Eisenhower to Bradley, 25 Jun, FUSA G-3 Jnl File"
2. A book: Eisenhower, *Crusade in Europe*, Doubleday, 1948
3. A government document: "Answers by Smith and Bull to questions by Hist Sec. ETOUSA, 14-15 Sep 45, OCMH Files"

The supplemental extractor needs to:
- Detect "cited in n. X" / "see n. X above" patterns
- Resolve the cross-reference to the target endnote's content
- Parse semicolon-separated sources within a single endnote into separate supplemental material entries

## Fix

### Step 1: Fetch endnote text (Phase 1 or early Phase 2)
For each footnote URL in the parsed file:
1. Fetch the HTML page (one page per chapter, contains all endnotes)
2. Parse each `#fnN` anchor to extract that endnote's text
3. Store in the parsed JSON: `{"number": 8, "url": "...", "text": "See, for example, SHAEF to AGWAR..."}`

### Step 2: Resolve cross-references
After fetching all endnotes for a chapter:
1. Detect patterns like "cited in n. 5" / "see n. 5, above"
2. Append the referenced endnote's text to the referring endnote
3. This must happen before passing to Grok so it has the full context

### Step 3: Include actual text in supplemental prompt
Change the prompt from:
```
Endnote References Found: [4, 5, 6]
```
To:
```
Endnote References:
  4: "Ruppenthal, Logistical Support, I, 421, 422."
  5: "Ltr, Eisenhower to Bradley, 25 Jun, FUSA G-3 Jnl File; Eisenhower, Crusade in Europe (1948), pp 245, 265; Answers by Smith and Bull, 14-15 Sep 45, OCMH Files."
  10: "See Ltr, Eisenhower to Bradley, cited in n. 5. [Resolved: same sources as endnote 5]"
```

### Step 4: Instruct Grok to parse semicolon-separated sources
A single endnote may contain multiple sources separated by semicolons. Each should become a separate `Supplemental_Material` entry with its own citation.

## Files to Modify

- `src/parser.py` or new `src/utils/endnote_fetcher.py` — fetch + parse endnote HTML
- `src/extraction/supplemental.py` — `create_supplemental_prompt()` to include endnote text
- Parsed JSON schema — add `text` field to footnotes array

## Notes

- Endnote pages are per-chapter, one fetch gets all endnotes for that chapter
- Pages are on ibiblio.org (public domain, no rate limiting concerns)
- Could cache fetched endnote text to avoid re-fetching
- Example URL pattern: `https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn1.html`
