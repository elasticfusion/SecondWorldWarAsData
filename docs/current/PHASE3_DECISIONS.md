# Phase 3 decisions (as of 2026-11-25)

One designation per question, kept stable so each can be answered on its own. Details and evidence for
each are in `docs/current/TODO.md` (search for the designation or the topic). Suggested order: the data
decisions first (D6-D9: they shape the schema), then the bibliography/NARA ones
(D10-D16: the bibliography is the core of the system).

## Decided

- **D1** - Should Phase 3 hold or continue when the bibliography merge fails or times out?
  PARKED until D2 is built: with D2 a content problem no longer fails the merge, so only an outage (S3
  error, task killed) is left to decide. Options: D1-a alert and continue (today); D1-b hold only the
  bibliography steps, book stays queued. Observed once (2026-10-28): a timeout from missing input (the
  batch poller was disabled; it is enabled now), not a merge failure.
- **D2** - Schema upgrades (owner 2026-11-25). Forward-compatible changes are brought forward on the fly
  by one shared library before a merge or update; non-forward-compatible changes only in an upgrade
  release. D2-Q1: the release script is used on dev too (run by hand). D2-Q2: while a breaking upgrade is
  pending nothing runs on the affected data; the work is queued and run after the upgrade. D2-Q3: all
  entities. D2-7: every merge path brings both records forward first and keeps a failing record out
  of the merge with an alert. (TODO: "Schema upgrades: forward-compatible on the fly ...")
- **D3** - Merges are exact only; anything similar goes to the Dedup Review UI to approve or deny (owner
  2026-11-25). D3-Q1, "exact": the same text ignoring case, whitespace and markdown emphasis.
  Transliteration, the canonical unit key, similarity ratios, abbreviations and synonyms are NOT exact.
  Add new rules to the rule log as cases come up. (TODO: "Merges: exact only ...")

## Answered 2026-11-25 (owner comments kept below, under each question)

- **D4 / D5** - NOT Phase 3 (owner 2026-11-25): the site database (PostgreSQL) has not been built. Moved to the
  TODO, "Site database (PostgreSQL, not built yet)". D5 decided there: a merged-away id gets a redirect placeholder.
- **D6** - merge scripts must work against S3 (the authoritative store: merge pointers, backups); a local mode
  is optional, kept only where it is easy.
- **D7** - a held document beats every other source. Add a way to note a copy's problems (missing pages,
  illegible, ...) so copies from other sites can be compared.
- **D8 / D9** - every fact carries a **sourcing classification** (the schema label; it says where a fact came
  from, not whether it is accurate: a primary document can be wrong). Needs a schema revision.

  | Label | Meaning |
  | --- | --- |
  | Primary | Original document or contemporary record |
  | Secondary, cited | Secondary account that provides footnotes or citations to its sources |
  | Secondary, uncited | Secondary account with no footnotes or source citations |
  | Unsourced | Assertion or data presented with no indication of origin |
  | Unlocatable | A specific source is named or referenced, but the document itself cannot be found |

  Unsourced facts are kept, classified Unsourced. When Grok cites a source, fetch it and evaluate it (prompts
  engineered to ask Grok for its sources). `people_groups` (units) first.
- **D10** - fix the copyright rule (cutoff moves with the current year; the "death + 70" off-by-one); no
  further confirmation needed.
- **D11** - a NARA entry with no scanned pages gets a status of its own (`reference_identified`), for human
  follow-up.
- **D13** - one search order per kind of citation (cheap/free sources first; each searched once; Grok's own web
  search last and only when nothing was found):
  archival record: record-group lookup -> NARA -> archive.org -> OpenSERP -> Grok;
  book: held in our corpus -> archive.org -> Gutenberg (public domain only) -> OpenSERP -> Grok, then book data
  from Open Library (D13-Q4, free API: ISBN, publisher, date, editions), Amazon only when all else fails
  (D13-Q3: today Amazon runs for every book and stores only the listing link);
  article: archive.org -> OpenSERP -> Grok.
  Fixes on the way: the book path's Gutenberg/OpenSERP searches call an OpenSERP address that does not exist.
  **D13-Q5 editions:** every edition discovered (a held copy, an online copy, Open Library, Amazon) is logged on
  the bibliographic record with where it was found; a citation of the record names the edition when known.
  Extend the schema for it. Most cited works predate ISBNs (1970): a reprint's ISBN belongs to the reprint's
  edition entry, never to the cited edition.
- **D14** - yes: every positive find is checked against its page content.
- **D16 / D27** - one writer at a time per shared record, enforced (record lock); tasks for different books
  may run concurrently. A book already submitted is not run again: a second copy is put on hold for the GUI to
  decide, unless there is a technical reason to re-run it (e.g. an improved LLM). That is a Phase 0 task
  (intake): today Phase 0 rejects the same work under a new title (`duplicate`) and a renamed re-upload
  (`source-renamed`), and re-processes a re-upload under the same slug.
- **D17** - human-submitted and Wikipedia finds are trusted; an OpenSERP find is Grok-validated, then
  human-validated; a Grok find is re-checked by Grok as a separate job, then human-evaluated.
- **D20** - yes: follow the links on a Grok-verified page.
- **D21** - the OpenSERP scope reduction is REJECTED: OpenSERP stays broad (images, unit histories, web pages,
  documents); Grok is added as a second finder of reference links; every find goes through Grok validation, then
  human review, nothing is added automatically; the landing-page -> document resolution (design doc section 5a)
  is kept because it widens what OpenSERP finds; the review UI is deferred.
- **D28** - keep the 300 s wait.
- **D29** - yes: confirm each open blocklist's licence first.
- **D30-D36** - after the rest of the fixes.
- **Waiting items** - keep waiting until Phase 3 is fully QA'd.

## Still to answer

- **D6-Q** - delete the five archived merge scripts?
- **D12** - keep the full reference in `archive_reference_number` (it holds the entry/box/file a microfiche pull
  needs) and add a separate `record_group` number field? Suggested: yes.
- **D13-Q2** - books and articles stop at the first copy found today (archival records keep every copy): keep
  searching for books and articles too (fits D7, comparing copies; costs more searches per record)?
- **D15** - where is the record group index you provided? (Not found in the repo; the code's lookup is built from
  NARA's online Record Group Explorer.)
- **D18** - store the links between chapters of the same HyperWar volume (e.g. "see page 329")?
- **D19** - read each chapter's links once and route them (one step instead of two; same behaviour)?
- **D22** - wire up the parallel OpenSERP workers (SQS), or retire them?
- **D23** - retire `map_features` (GeoJSON of what is drawn inside a map; conflicts with D1 of 2026-11-17,
  "appearance links only")?
- **D24** - retire `supplemental_info_pipeline` (only an archived script calls it; Phase 2's supplemental
  extraction does the job)?
- **D25** - put the NOAA key in the existing Secrets Manager secret and read it from there (and from `.env`
  locally)?
- **D26** - scope the "dedup complete" Phase 3 run to the books whose data changed?

## Open (the original questions, with owner comments)

### Data and schema
- **D4** - The database loader never deletes a removed record: rebuild into a fresh schema and swap, or
  mark the rows each load touched and delete the rest?

> Which database?  DynamoDB should only be used for queues.  This has been decided previously.

- **D5** - Old ids on the site and API: a merged-away id redirects to the record it was merged into,
  shows as an alias, or returns "gone"?

> You can leave a placeholder to redirect.

- **D6** - Hand-run merge scripts: keep or delete the five archived ones; may the active ones run on S3
  or only on local files?

> S3 is critical.  

- **D7** - Several sources per document (held PDF, NARA copy, archive.org): a `sources` list per
  bibliography record, and the rule for which source wins.

> held document beats other sources.  We may want to update the bibliographical to allow for noting documents with issues (missing pages, illegible copy, etc..) so we can compare documents from other sites.

- **D8** - Unit facts Grok gives from model memory (no source): keep them flagged unsourced, or drop
  them?

> I assume 'unit' = 'people_groups', unsourced records should be kept, however in the past we discussed the idea of a rated source.  Primary, secondary with footnotes, secondary without footnotes, unsourced data.  The data grok outputs, if unsourced, should be treated that way.  If Grok has sourced it, then we should follow up grab that link and evaluate it.  Prompts should be engineered for this outcome.

- **D9** - Facts from a URL that cannot be cited: drop them, or keep them flagged?

> I'd add 6th category to D8, unavailable.

### Bibliography and NARA
- **D10** - Copyright rule: move the cutoff year with the current year; fix the off-by-one in "death +
  70 years". Confirm the rule before the change.

No, you don't have to confirm 

- **D11** - A NARA catalog entry with no scanned pages: still "resolved", or a new status
  (`reference_identified`)?

> Yes, it may need human intervention

- **D12** - Store `archive_reference_number` as the bare record-group number? (Changes stored data.)

> We will use that to physically retrieve data from NARA microfiche.  How would it change anything? 

- **D13** - Search order record group -> NARA -> archive.org -> Gutenberg -> OpenSERP -> Grok (your
  order; more OpenSERP and Grok calls).

> This seems NARA focused.  Gutenberg will certainly not have it.  why would Openserp and Grok be repeated?

- **D14** - Check every positive find against the page content (one more page fetch + one Grok call per
  find)?

Yes.

- **D15** - NARA holding address per record group: a table only you can supply, or leave it empty?

> I don't understand this.  I've provided a record group index, you can search against likely criteria.

- **D16** - A Phase 3 run launched by hand while another holds the lock: wait, refuse, or take over?

> Wait, but concurrent would be better.

### Links and maps
- **D17** - Map URLs through human review: what a map review item contains; which map hosts are trusted.

> Anything human submitted it trusted, wikipedia is trusted.  Anything found via OpenSerp should be Grok validated, then human validated.  Anything found throug grok should be resubmitted to grok as a separate job, then human evaluated.

- **D18** - Store the links between pages of the same volume (today only counted)?

>  yes, I think, but I thought we stopped breaking into individual pages?

- **D19** - Merge the two document-link steps into one?

> I don't understand this.

- **D20** - Follow the links on a Grok-verified page? (New feature; two Grok checks per link.)

> yes.

- **D21** - The OpenSERP scope reduction design (`docs/current/design/OPENSERP_SCOPE_REDUCTION.md`).

See comments in the doc. 

### Wire up or retire
- **D22** - The SQS OpenSERP path (nothing calls it).

> OpenSerp finds a Document, webpage image, it submits it to a queue for grok validation (then human validation)

- **D23** - The `map_features` step (no Phase 3 step).

> I'm assuming this is the submot to grok vision step.

- **D24** - `supplemental_info_pipeline` (nothing calls it).

> Not sure what this is, 

- **D25** - NOAA weather: supply a token, or retire the step?

> .env now has NOAA_API_KEY

### Pipeline and runs
- **D26** - The "dedup complete" event starts Phase 3 for every book with no book named: should it re-run
  paid steps over the whole corpus?

> How could this happen if everything is being validated?

- **D27** - Two tasks overwriting the same shared records: conditional S3 writes, or one writer at a time?

One at a time, enforced

- **D28** - How long Phase 3 waits for the bibliography merge (300 s today)?

> leave that value.

- **D29** - Tiered link-safety check: confirm each open blocklist's licence first.

> Yes

### Spend or access approvals

> let address this after working therought the rest of fixes.

- **D30** - The Group 0 baseline run, then a test run per group (Grok spend).
- **D31** - Turn on Grok batching for Phase 3 after a validation run (changes spend; adds per-request
  identity checks).
- **D32** - Re-run equipment vision under the "any photograph of the model" rule (Grok spend).
- **D33** - Geocode miss-recovery: native-name retry, OpenSERP rescue, photo GPS (spend).
- **D34** - Re-check the 5,621 older cached Grok answers (reads the xAI key; retrieval is free).
- **D35** - Spot-loss and failover tests on dev (deploy + spend).
- **D36** - A GitHub OIDC role for deploy-on-merge.

## Waiting on other work

> continue to wait until Phase 3 has been fully QA'd

- The Group 6 place-hierarchy items (incl. Pacific -> Asia): wait on the Phase 2 Wikidata fix (Track P,
  "Places: the hierarchy from the Phase 2 Wikipedia/Grokipedia lookup is guessed").
- All UI work: deferred to the end (owner 2026-11-24).
