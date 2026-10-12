# Source Qualification — design

**Status:** design agreed 2026-10-12 (owner). Schema change pending implementation.
**Purpose:** let a reader (and the future RAG ranking + UI) judge *how a source qualifies* for an
asserted fact — **qualification, not veracity**. "Primary" does not make a claim true; it qualifies
it as a first-hand account. The reader must be in a position to make a quality judgement.

## Two orthogonal axes (one value each; derived, not fabricated)

1. **`source_type`** — relationship to the event:
   `primary | secondary | tertiary | reference | unknown`.
2. **Qualification detail**, meaningful depending on type:
   - `proximity` (for primary): `participant | witness | contemporaneous | recollection | n/a`
     — an after-action report filed next day vs. a 1970s memoir are BOTH primary but qualify
     differently.
   - `evidentiary_support` (for secondary/tertiary): `dense | light | none | n/a` — the
     footnote/apparatus axis (Green Book heavily footnoted = dense; popular history with a
     bibliography but few footnotes = light; online article asserting facts citing nothing = none).

Each qualifier also records HOW it was derived so the signal is itself trustworthy:
`method` (`derived | llm_assisted | manual`), `basis` (free text, e.g. "NARA RG407" or
"footnote_density=0.04"), `confidence` (`high | medium | low`).

## Derivation ladder (deterministic-first; keys on signals already captured)

```
archive_reference_number present (NARA/archival)        -> primary / contemporaneous   (derived)
author = named participant + memoir/unit-history        -> primary / recollection       (llm_assisted)
footnote_density >= dense_threshold AND bibliography     -> secondary / dense            (derived)
bibliography present AND footnote_density low            -> secondary / light            (derived)
web-sourced AND no citations                             -> secondary|tertiary / none    (derived)
else                                                     -> unknown  (flag for review)
```

`footnote_density` = `len(parsed.footnotes)` normalized by paragraph/page count (per `parsed.json`),
rolled up to the work. Only the participant/recollection rung needs LLM judgement; it is
flagged-for-review rather than silently trusted. Consistent with "source=content authority,
flag-not-fabricate".

## Where it lives (identity authority = bibliography)

- Stored ONCE on the **bibliography record** (`src/schemas/bibliography_output.py`) as an additive,
  nullable `source_qualification` object. No new schema file; additive bump
  `bibliography` 2.25 -> 2.26 + re-pin fingerprint; **no reprocessing** (existing records stay
  valid). NOT duplicated onto `parsed.json` or `event.json` (would drift — the referential-integrity
  discipline).
- The one thing that touches the chapter layer is the raw **footnote-density measurement** feeding
  the rollup (the `footnotes` array already lives in `parsed.json`).

## Access path to RAG / UI (TBD UI does not block the schema work)

A claim is surfaced to the reader at the EVENT layer, not the bibliography file. The qualifier is
read **through the existing link** rather than copied:

```
event -> mentions[].BibliographyID -> bibliography.source_qualification
```

- `src/loader/transform.py` already threads `source_id` (= `BibliographyID`) to the serving layer
  (and already reads a `source_type` key for other entities), so the qualifier rides an existing
  rail. When the loader populates Postgres/pgvector (FUTURE RAG phase, per the steering docs), the
  qualifier becomes a **queryable column/filter on the source dimension**: RAG ranks
  primary+corroborated above undocumented-web; the UI facets/badges by it and can show `basis`/
  `confidence` so the reader sees WHY a source qualifies.
- Because it is a first-class enum from day one, it is filterable the moment it is written; the TBD
  UI only has to expose a field that already exists. Materializing the qualifier directly onto
  events (a second additive bump) is a later query-performance optimization, NOT a requirement —
  deferred until RAG ranking needs it (build RAG against the stable corpus).

## Separation of concerns

- **Qualification** (this doc) = property of the SOURCE, stable, assigned once.
- **Veracity / confidence** = property of a CLAIM, built from corroboration ACROSS sources (e.g.
  "three independent primary sources agree"). Different signal; do not conflate. `semantic_flags`
  is the claim-level advisory layer.

## Standards leverage — Dublin Core (OPTION, not yet decided)

Research (2026-10-12) of existing standards: there is **no single standard that classifies exactly
this** (source_type + evidence quality together). What exists and is leverageable:

- **Primary / secondary / tertiary IS the de-facto standard** for the `source_type` axis — the
  shared historiographical + library-science classification (used by NARA, archives, universities).
  It is a convention, not an ISO-numbered controlled enumeration.
- **Dublin Core (DCMI Metadata Terms)** is THE widely-adopted descriptive-metadata standard, and
  **NARA explicitly encourages controlled vocabularies / DCMI** — relevant because NARA is this
  corpus's primary-source custodian. DCMI is designed to be used "in combination with other
  vocabularies in the context of **application profiles**", which is exactly our mixed case.
- **No formal controlled vocabulary exists for the footnote/evidence-quality axis**
  (`evidentiary_support`). Only assessment *rubrics* exist — **CRAAP** (Currency, Relevance,
  Authority, Accuracy, Purpose), SIFT, GRADE — not enumerated metadata values. So this axis is a
  justified LOCAL extension, grounded in CRAAP's Accuracy/Authority criteria to stay defensible.

### What DCMI covers vs. does NOT

- `dcterms:provenance` — custody/provenance statement (we already capture via
  `archive_reference_number` / `archive_physical_address`).
- `dcterms:source` — the resource the current one is derived from.
- `dcterms:bibliographicCitation` — the citation (we already capture in `citation`).
- `dcterms:type` + the **DCMI Type Vocabulary** (`Text | Dataset | Image | MovingImage | Event |
  Collection | Sound | StillImage | …`) — this is **genre/media, NOT primary/secondary**. It maps to
  our existing `SourceMetadata.media_type` axis, not to qualification.
- `dcterms:audience`, `dcterms:accrualMethod` — adjacent, optional.
- ❗ DCMI has **no** term for primary/secondary/tertiary and **no** term for evidence/footnote
  quality. Those remain our vocabulary.

### Two ways to leverage it

**Option A — Direct adoption (interoperability-first).** Name/shape the stored fields as a Dublin
Core application profile and emit `dcterms:*` directly:
- map `source_type` ⇄ a profile-defined term (DCMI has none, so a local `wwii:sourceType` term in a
  documented profile), `media_type` ⇄ `dcterms:type` (DCMI Type Vocabulary), provenance ⇄
  `dcterms:provenance`, citation ⇄ `dcterms:bibliographicCitation`, derived-from ⇄ `dcterms:source`.
- PRO: export/Linked-Data ready, NARA-aligned, future harvesting (OAI-PMH) trivial.
- CON: heavier now; most DCMI terms are genre/custody, so the qualification core is still local.

**Option B — Derivation / mapping (local-first, map on export).** Keep the compact local
`source_qualification` object (above) as the authoritative internal shape; provide a **one-way
crosswalk to Dublin Core at export/serve time** (a `to_dublin_core()` in the loader):
- `media_type → dcterms:type (DCMIType)`, `archive_reference_number → dcterms:provenance`,
  `citation → dcterms:bibliographicCitation`, parent work → `dcterms:source`; `source_type` /
  `evidentiary_support` / `proximity` emitted as documented profile-extension terms.
- PRO: minimal schema now, no premature coupling, still standards-exportable when RAG/UI/export lands.
- CON: the DCMI representation is generated, not the stored form.

**Recommendation:** Option B — store the compact local qualifier now (additive bibliography bump,
no reprocess), and add the Dublin Core crosswalk as a derivation in the loader when the
export/RAG/UI layer is built. This leverages the standard (and NARA alignment + Linked-Data export)
without over-building before the UI specs exist, consistent with "build RAG against the stable
corpus." Decision pending owner sign-off.
