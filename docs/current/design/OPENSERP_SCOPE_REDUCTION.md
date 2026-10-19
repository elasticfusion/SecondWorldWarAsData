# OpenSERP Scope Reduction — PDF/EPUB Source Discovery + Human-Gated Acquisition

**Status:** Proposal (operator-directed, 2026-10-19). **Supersedes** the broad Phase-3 OpenSERP
enrichment with a narrow, human-gated source-acquisition flow. **Related:** A00 (OpenSERP redesign),
A0/A0+ (parallelize enrichment). **Author:** pipeline agent.

> Driver: 48h Container Insights data shows `dev-wwii-openserp` is the one real CPU bottleneck (peak
> 98.7%, avg 30.6%, near its 1-vCPU ceiling) and the runaway serial tail (killed a ~15h Phase-3 run).
> The broad 5-entity-type web/image enrichment delivers low marginal value for high cost.

---

## 1. The change in one sentence

Stop using OpenSERP to broadly enrich five entity types with web pages + images; instead use it for
**one focused job — finding acquirable source documents (PDF/EPUB) on a topic — pair each hit with
Grok-proposed candidate links, and route BOTH into a human review that confirms (incorporate into the
dataset) or discards.**

---

## 2. Today vs. proposed

### Today (Phase 3, step 5/6 — `_run_openserp_enrichment`)
`enrich_{people,places,groups,equipment,source_sections}_with_openserp` each run OpenSERP web + image
searches serially (`rate_limit_seconds: 5`, `max_images_per_entity: 3`, `max_web_results: 3`). Results
are auto-incorporated as enrichment. Fan-out = 5 types × N entities × up to 6 queries × 5s spacing →
hours; CPU-pegged; no human gate.

### Proposed
```
  TOPIC (per bibliography entry / unresolved source / entity needing a citation)
        │
        ├── OpenSERP  → PDF/EPUB DOCUMENTS ONLY  (reuse _openserp_fetcher: already rejects HTML,
        │                                          accepts .pdf/.epub/.doc/.txt/... direct docs)
        │
        └── Grok      → candidate links on the topic (titles + URLs it believes address it)
                                   │
                                   ▼
                     ┌───────────────────────────────┐
                     │ HUMAN REVIEW QUEUE             │  (mirror dedup_ui / mdreview_ui)
                     │  • OpenSERP PDFs (verified)    │
                     │  • Grok-proposed candidates    │
                     │  → per item: CONFIRM / DISCARD │
                     └───────────────────────────────┘
                                   │ confirmed
                                   ▼
                   acquire + incorporate into the dataset
                   (bibliography entry resolved → resource_urls + acquisition target;
                    the existing acquisition pass fetches the confirmed doc)
```

**Net effect:** OpenSERP's role shrinks from "enrich everything" to "propose source documents for a
human to accept." No auto-incorporation; nothing enters the dataset without a confirm.

---

## 3. What already exists (this is mostly consolidation, not new build)

- **`_openserp_fetcher`** (`acquisition_fetchers.py`) currently accepts ONLY bare
  `.pdf/.epub/.doc/...`-suffixed URLs and **REJECTS HTML landing pages** — ⚠️ **this is a FLAW to
  FIX, not a feature to reuse.** It is asymmetric with `_archive_org_fetcher`, which *accepts* an
  item/details landing page and lets the download pass resolve the concrete file via the `/download/`
  listing. Most real source hits (archive.org / HathiTrust / Google Books / Gutenberg item pages,
  PDFs served via query-string or `Content-Disposition` rather than a `.pdf` suffix) ARE landing
  pages — the current rule discards the majority of acquirable sources. The redesigned discovery step
  must **accept landing pages and resolve them to the document** (follow the archive.org pattern:
  parse the page / download listing / `og:`/`<link rel>` metadata / `Content-Disposition` to find the
  real PDF/EPUB), not require a bare file-suffix URL. See §5a.
- **`bibliography_resolver._search_openserp_archive`** ALREADY runs an OpenSERP archive search +
  **Grok verification** (`_verify_openserp_match`) of each candidate's relevance to the citation.
- **Grok candidate proposal** — the HyperWar biblio generator + bibliography_resolver already have the
  "ask Grok for links, then validate" pattern (and the #453 SSRF guard / allowlist for scraped URLs).
- **Human-review surfaces** — `dedup_ui_handler`, `dedup_gate_handler`, `mdreview_ui_handler` + the
  `needs-review/` S3 prefix + a `review_queue.json` convention already exist to mirror.
- **Acquisition pass** — confirmed targets already have a fetch path (`AcquisitionTarget`, NARA /
  archive.org / Gutenberg / openserp fetchers).

So the work is: (a) demote/remove the broad per-entity web+image enrichment, (b) promote the
PDF/EPUB discovery + Grok-propose into a first-class "source candidates" step, (c) add the
confirm/discard review UI + the incorporate-on-confirm wiring.

---

## 4. Proposed flow (detail)

### Stage 1 — Candidate discovery (replaces step 5/6)
For each **topic** (start with bibliography entries lacking a resolved online copy; optionally
entities that need a citation):
1. **OpenSERP** search scoped to documents. Accept result pages AND landing pages, then **resolve to
   a concrete PDF/EPUB** (see §5a) — do NOT require a bare `.pdf` suffix. Keep only hits that resolve
   to a fetchable document.
2. **Grok** proposal: ask for candidate source links addressing the topic (title + URL + why).
   Validate URLs through the existing SSRF allowlist (#453); keep only PDF/EPUB-resolvable ones.
3. Write a **candidate record** per topic to a review queue (S3 `needs-review/sources/{topic}.json`):
   `{topic, candidates:[{source:openserp|grok, url, title, fmt, grok_rationale?, verify_score?}]}`.
4. OpenSERP runs **once per topic for documents only** — far fewer queries than 5-entity × images.

### Stage 2 — Human review (new UI, mirrors dedup_ui)
- Lists pending candidate records; per candidate: **Confirm** (incorporate) or **Discard**.
- Confirm writes the decision (e.g. `confirmed_sources#{topic}` + the chosen url/fmt); Discard records
  a `not_a_source` exclusion so it isn't re-proposed (mirror the dedup `not_*` exclusion pattern).
- Auth + stage pattern reused from the dedup UI (the `app` stage + `dedup-auth` secret model).

### Stage 3 — Incorporate on confirm
- A confirmed candidate becomes an `AcquisitionTarget` (fmt=pdf/epub, object_urls=[confirmed url]);
  the existing acquisition pass fetches it and attaches it to the bibliography entry
  (`resource_urls`, `search_status=resolved`, provenance of the human decision + source=openserp|grok).

---

## 5a. LANDING-PAGE RESOLUTION — correcting the HTML-rejection flaw (the key fix)

The single most important behavior change. The current `_openserp_fetcher` rejects any hit that
isn't a bare `.pdf/.epub` URL — which discards archive.org / HathiTrust / Google Books / Gutenberg
**item pages** (the normal shape of a real source) and PDFs served via query-string or
`Content-Disposition`. The discovery step must instead treat an **HTML landing page as a lead to a
document, not a reject**:

1. Accept the hit (item/result page, or direct file).
2. **Resolve** it to a concrete PDF/EPUB, in order of preference:
   - site-specific resolver (archive.org `/download/` listing — already done by
     `_archive_org_fetcher`; generalize that pattern to other hosts),
   - document links in the page (`<a href=*.pdf|*.epub>`, `<link rel="alternate" type="application/pdf">`),
   - metadata (`og:` tags, `citation_pdf_url`, or a HEAD request's `Content-Disposition`),
   - a bare `.pdf/.epub` suffix (the only case the old code handled).
3. The reject criterion is **"resolves to no reachable document,"** NOT "the URL is HTML." Keep a
   liveness/HEAD check so the human reviewer only sees candidates that resolve to a live document
   (§7.3).

This replaces the suffix-only rule in `_openserp_fetcher` and makes OpenSERP actually able to find
the sources the operator wants.

## 5. What gets DROPPED (be explicit — this is the value/cost call)

- **Web-page enrichment** for people/places/groups/equipment/source_sections (the `max_web_results`
  snippets). Grokipedia/Wikipedia (Phase-2, cheaper, already runs) remains the text-enrichment source.
- **Image search** for all entity types (`max_images_per_entity`). NOTE: if place/people IMAGES are
  user-facing on the site, confirm that upstream Phase-2 Grokipedia/Wikipedia media covers them before
  dropping — otherwise keep a narrow place/person image path. (Open question for the operator.)
- **Auto-incorporation** — nothing enters the dataset without a human confirm (a feature, not a loss:
  professional-historian audience demands source accuracy/provenance).

---

## 6. Benefits

- **Kills the CPU bottleneck + serial tail.** One documents-only OpenSERP query per topic instead of
  5 entity types × images × web results. Collapses the hours-long Phase-3 tail.
- **Higher data quality.** Human-gated acquisition fits the professional-historian/provenance goal;
  no auto-ingested noise; every source has a recorded confirm decision (audit trail).
- **Reuses existing code.** PDF/EPUB filter, Grok-verify, SSRF guard, review-UI pattern, acquisition
  fetchers all exist. Mostly wiring + one new review UI + removing the broad enrichment.
- **Cheaper + safer to parallelize later.** A documents-only, per-topic step is a clean unit for the
  A0/A0+ fan-out if throughput still matters.

## 7. Risks / open questions
1. **Images.** Does the site surface place/person images sourced from OpenSERP today? If yes, dropping
   image search loses a user-facing feature unless Phase-2 Grokipedia/Wikipedia media covers it.
   **Decide before cutting.** (Recommend: measure actual image coverage first.)
2. **Human-review throughput.** A confirm/discard gate adds human latency to source acquisition. Scope
   it to topics that actually need a source (unresolved bibliography entries), not every entity, or the
   queue is unmanageable. Batch the UI (confirm many at once).
3. **Grok link quality.** Grok-proposed URLs can hallucinate/404; the PDF/EPUB-resolvable + SSRF-
   allowlist + (optional) liveness-check gate must run BEFORE a candidate reaches the human, so
   reviewers don't waste time on dead links.
4. **Re-proposal churn.** Discards must persist (exclusion record) so the next run doesn't re-surface
   the same rejected candidate.

## 8. Non-goals
- Not changing Phase-1/Phase-2.
- Not changing NARA / archive.org / Gutenberg resolution (those stay; this is the OpenSERP + Grok leg).
- Not auto-incorporating anything (the whole point is the human gate).

## 9. Migration path (incremental)
1. **Measure first (do before building):** run the existing OpenSERP metrics per entity type — how
   many queries/enrichments each of the 5 types produces, and the actual image coverage — so the drop
   decision (esp. images, §7.1) is data-driven. (Can reuse `_write_openserp_metrics` output.)
2. **Disable the low-value legs** (equipment/groups/source_sections web+image) behind flags — immediate
   relief, reversible, no new code.
3. **Promote PDF/EPUB discovery + Grok-propose** into a candidate-writing step (reuse
   `_openserp_fetcher` + `_verify_openserp_match` + SSRF guard) → `needs-review/sources/`.
4. **Build the confirm/discard review UI** (fork dedup_ui) + the exclusion-on-discard record.
5. **Wire incorporate-on-confirm** to the acquisition pass.
6. **Remove** the broad per-entity OpenSERP enrichment once the new flow is validated.

Step 1–2 are cheap/reversible and give immediate bottleneck relief; 3–6 build the human-gated flow.

## 10. Recommendation / sequencing
- Don't start mid-baseline-run. After the current XChannel E2E, do **step 1 (measure) then step 2
  (flag-off low-value legs)** for immediate relief, and decide the images question (§7.1) from data.
  Build the human-gated discovery (3–5) once the measurements confirm the value tradeoff.
