# OpenSERP Scope Reduction — PDF/EPUB Source Discovery + Human-Gated Acquisition

**Status:** Proposal (operator-directed, 2026-10-19). **Supersedes** the broad Phase-3 OpenSERP
enrichment with a narrow, human-gated source-acquisition flow. **Related:** A00 (OpenSERP redesign),
A0/A0+ (parallelize enrichment). **Author:** pipeline agent.

> Driver: 48h Container Insights data shows `dev-wwii-openserp` is the one real CPU bottleneck (peak
> 98.7%, avg 30.6%, near its 1-vCPU ceiling) and the runaway serial tail (killed a ~15h Phase-3 run).
> The broad 5-entity-type web/image enrichment delivers low marginal value for high cost.

---

## 1. The change in one sentence

Stop using OpenSERP to broadly enrich five entity types with web pages + images. Split the job by
tool strength: **OpenSERP finds acquirable source DOCUMENTS (PDF/EPUB)** on a topic; **Grok proposes
authoritative HTML REFERENCE links** on the same topic. Route BOTH into one human review that **ADDs or DISCARDs** each candidate — ADD imports the page(s)
(confirmed documents fetched into the corpus; confirmed HTML references stored as citation links,
**no snapshot**, UI renders a **clickable link** to investigate); DISCARD rejects and suppresses
re-presentation for **at least 90 days**.

---

## 2. Today vs. proposed

### Today (Phase 3, step 5/6 — `_run_openserp_enrichment`)
`enrich_{people,places,groups,equipment,source_sections}_with_openserp` each run OpenSERP web + image
searches serially (`rate_limit_seconds: 5`, `max_images_per_entity: 3`, `max_web_results: 3`). Results
are auto-incorporated as enrichment. Fan-out = 5 types × N entities × up to 6 queries × 5s spacing →
hours; CPU-pegged; no human gate.

### Proposed — split by tool strength
```
  TOPIC (e.g. "US 4th Infantry Division"; per bibliography entry / unresolved source / entity)
        │
        ├── OpenSERP → ACQUIRABLE DOCUMENTS (PDF/EPUB)         ── search-engine strength: "find the file"
        │              accept item/landing pages + RESOLVE to the
        │              document (§5a); HTML-with-no-document is OUT of scope (that is Grok's lane)
        │
        └── Grok     → AUTHORITATIVE HTML REFERENCES            ── knowledge strength: "who is authoritative"
                       e.g. the 4th Inf Div Association history site (4thinfantry.org)
                       → URL + title + description (NO fetch, NO snapshot)
                                   │
                                   ▼
                     ┌─────────────────────────────────────────────┐
                     │ HUMAN REVIEW QUEUE  (mirror dedup_ui)        │
                     │  • OpenSERP document candidates (resolved)   │
                     │  • Grok HTML reference candidates            │
                     │  UI renders a CLICKABLE LINK per candidate   │
                     │  → per item: CONFIRM / DISCARD               │
                     └─────────────────────────────────────────────┘
                           │ confirmed                 │ confirmed
                           ▼ (document)                ▼ (HTML reference)
                 acquire + ingest PDF/EPUB     store as reference/citation link
                 (AcquisitionTarget →          (resource_urls / reference entry;
                  acquisition pass fetches)     URL + description, NOT fetched)
```

**Net effect:** OpenSERP shrinks from "enrich everything" to "propose acquirable documents"; Grok
owns the HTML-reference lane; nothing enters the dataset without a human confirm. The 4th-Division
association website IS surfaced — via Grok, as a reference link the reviewer can click — rather than
missed (it would never be an OpenSERP PDF/EPUB hit).

---

## 3. What already exists (this is mostly consolidation, not new build)

- **`_openserp_fetcher`** (`acquisition_fetchers.py`) accepts only bare `.pdf/.epub/...`-suffixed URLs.
  Under this split, **rejecting a pure-HTML reference site is now CORRECT** (HTML is Grok's lane, §1).
  The ONLY remaining fix is narrower: it must still accept an **item/landing page that DOES lead to a
  downloadable PDF/EPUB** (archive.org / HathiTrust / Google Books item pages) and **resolve it to the
  file** — today the suffix-only rule wrongly drops those, and it is asymmetric with
  `_archive_org_fetcher` (which accepts an item page + resolves via the `/download/` listing). So:
  resolve item→document (§5a); drop only hits that resolve to no fetchable document. A plain HTML
  reference with no document is correctly NOT an OpenSERP result (Grok proposes it instead).
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
entities that need a citation, e.g. a unit like "US 4th Infantry Division"):
1. **OpenSERP → documents.** Search scoped to documents; accept result/landing pages and **resolve to
   a concrete PDF/EPUB** (§5a). Keep only hits that resolve to a fetchable document. (A pure-HTML page
   with no document is NOT an OpenSERP candidate — Grok handles that.)
2. **Grok → HTML references.** Ask Grok for authoritative links on the topic (title + URL +
   description/why). These are the HTML references (association sites, unit histories, archives'
   landing pages). Validate URLs through the SSRF allowlist (#453) + a liveness/HEAD check so dead
   links don't reach the reviewer. **Not fetched, not snapshotted** — stored as reference candidates.
3. Write a **candidate record** per topic to the review queue (S3 `needs-review/sources/{topic}.json`):
   `{topic, candidates:[{kind: "document"|"reference", source: openserp|grok, url, title,
   description?, fmt?, resolved_doc_url?, verify_score?, live: bool}]}`.
4. OpenSERP runs **once per topic for documents only** — far fewer queries than 5-entity × images.

### Stage 2 — Human review (new UI, mirrors dedup_ui)
- Lists pending candidate records; **each candidate renders as a CLICKABLE LINK** (open in new tab) so
  the reviewer can investigate the actual page/document before deciding. Show kind (document vs
  reference), source (openserp/grok), title, description, and live-check status.
- Two buttons per candidate (or per selected group of pages): **ADD** and **DISCARD**.
  - **ADD** → imports the page(s):
    - a **document** candidate → queued for acquisition (ingest the PDF/EPUB into the corpus);
    - a **reference** candidate → stored as a citation/reference link (URL + title + description),
      NOT fetched.
  - **DISCARD** → rejects the page(s) and **suppresses re-presentation for at least 90 days.** Write
    a TTL'd exclusion record (e.g. `source_discard#{topic}#{url_hash}` with
    `ttl = now + 90*86400`), so the discovery step skips any candidate whose (topic, url) has a live
    discard record. This is a "not now," NOT a permanent block — after 90 days the candidate MAY be
    re-surfaced (a document may have appeared since, a dead link may have recovered, Grok may propose
    a better rationale). (Distinct from a permanent block, which we could add later if needed.)
- Operate on single pages or multi-select (ADD/DISCARD a group at once) to keep the queue manageable.
- Auth + stage pattern reused from the dedup UI (the `app` stage + `dedup-auth` secret model).

### Stage 3 — Incorporate on ADD (two paths)
- **Document (OpenSERP):** added candidate → `AcquisitionTarget` (fmt=pdf/epub,
  object_urls=[resolved_doc_url]); the existing acquisition pass fetches it and attaches it to the
  bibliography entry (`resource_urls`, `search_status=resolved`).
- **Reference (Grok):** added candidate → appended as a reference/citation link on the entry (URL
  + title + description), **never fetched**. The link lives in the dataset for the site/API to surface.
- Both record provenance: `source=openserp|grok`, the human decision (ADD) + reviewer + timestamp.

---

## 5a. ITEM/LANDING-PAGE → DOCUMENT RESOLUTION (the OpenSERP fix)

Within OpenSERP's **document lane**, the fix is to resolve item/landing pages that DO lead to a
PDF/EPUB — not to require a bare file suffix. (This is NOT about accepting HTML reference sites; those
are Grok's lane, §1.) The current `_openserp_fetcher` rejects anything that isn't a bare `.pdf/.epub`
URL, which wrongly discards archive.org / HathiTrust / Google Books / Gutenberg **item pages** that
resolve to a real document, and PDFs served via query-string or `Content-Disposition`. The discovery
step must treat such an **item page as a lead to a document**:

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
3. **Candidate quality before the human.** Grok-proposed HTML references can hallucinate/404 and
   OpenSERP doc hits can fail to resolve. Pre-filter BEFORE the review queue: Grok references →
   SSRF-allowlist + liveness/HEAD check (so every reference the UI shows is a live, clickable link);
   OpenSERP docs → must resolve to a reachable PDF/EPUB (§5a). Reviewers never see dead links.
4. **Re-proposal churn / discard window.** Discards are TTL'd (≥90 days), not permanent — a
   discarded (topic, url) must be suppressed until its TTL expires, then MAY re-surface (intentional:
   a document may have since appeared, a dead link recovered). Ensure the discovery step reads live
   discard records and skips matching candidates; after 90 days re-surfacing is expected, not a bug.
   (If a reviewer wants to *never* see an item again, offer a separate explicit permanent-block action
   — out of scope for v1.)

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
4. **Build the ADD/DISCARD review UI** (fork dedup_ui) with clickable per-candidate links + the
   TTL'd (≥90-day) discard-exclusion record.
5. **Wire incorporate-on-confirm** to the acquisition pass.
6. **Remove** the broad per-entity OpenSERP enrichment once the new flow is validated.

Step 1–2 are cheap/reversible and give immediate bottleneck relief; 3–6 build the human-gated flow.

## 10. Recommendation / sequencing
- Don't start mid-baseline-run. After the current XChannel E2E, do **step 1 (measure) then step 2
  (flag-off low-value legs)** for immediate relief, and decide the images question (§7.1) from data.
  Build the human-gated discovery (3–5) once the measurements confirm the value tradeoff.
