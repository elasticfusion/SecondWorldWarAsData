# Project: Second World War As Data

This project revolves around using WWII unstructured data and turning it into structured, citable data. 

The long-term goal is a low-cost, mostly-static website backed by this data, with a
continuous stream of new data added over time, and eventually a paid API.

In addition to the static data, there will be RAG for AI queries. Allowing users to interact with the data.

## Current focus

Finish the data ingestion and validating data quality **before** building the RAG/search
layer. RAG is a derived layer that sits on top of the extracted data, so it
does not block current work and should be built against the *complete*,
schema-stable corpus to avoid re-embedding.

## Two distinct ingestion tracks

The project handles two categorically different kinds of source, and they must
be handled differently — do not force structured data through the narrative
LLM-extraction pipeline.

1. **Narrative sources** (`output/`) — free-text primary records (after-action
   reports, unit journals, German KTBs, combat interviews, official "green
   book" histories). Handled by LLM **extraction** into typed entities, then
   embedded for semantic retrieval. Uncertainty is inherent.  Languages vary.

2. **Structured / reference data** (e.g. the ETO Order of Battle under
   `contentrepository/`) — already-tabular reference material trapped in a
   printed book. Handled by **parsing** into relational rows + a focused
   **OCR/AI cleanup pass**, NOT open-ended LLM extraction. The OCR+AI markdown
   is the **source of truth**; the PDF is kept for citation and re-OCR; the
   CSVs are a **regenerable derivation** of the markdown, not authoritative.

## Data model (established, do not relearn)

- Entities are **event-centric**, keyed on `EventID` / `Sub-eventID`, with a
  `mentions` / `event_mentions` junction linking entities to events and sources.
- **ULID** primary keys throughout (`PersonID`, `PlaceID`, `CasualtyID`,
  `BibliographyID`, `ImageID`, `MentionID`, ...).
- Rich **provenance** is already captured: `verbatim_reference`,
  `archive_reference_number` (NARA), `license`, `copyright_status`,
  `original_text`, page/line numbers. This is citation-ready.
- Records are versioned via `_schema_version` (currently 2.6) and
  `_last_updated` — usable for incremental ingestion / change detection.

## Audiences (drives features and, later, pricing tiers)

- **Professional historians** — need source accuracy, citations, provenance
  (`bibliography` + `verbatim_reference` + `archive_reference_number`). Likely
  paid / API tier.
- **Amateur historians** — browse generated pages, images, maps, semantic
  search. Free tier / traffic base.
- **Genealogical searchers** — entity-centric lookups on people / units /
  places / dates. Monetize via features (saved searches, new-record alerts)
  more than raw request volume.
