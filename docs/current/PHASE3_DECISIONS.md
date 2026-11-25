# Phase 3 decisions (as of 2026-11-25)

One designation per question, kept stable so each can be answered on its own. Details and evidence for
each are in `docs/current/TODO.md` (search for the designation or the topic). Suggested order: the data
decisions first (D4-D9: they shape the schema and the database), then the bibliography/NARA ones
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

## Open

### Data and schema
- **D4** - The database loader never deletes a removed record: rebuild into a fresh schema and swap, or
  mark the rows each load touched and delete the rest?
- **D5** - Old ids on the site and API: a merged-away id redirects to the record it was merged into,
  shows as an alias, or returns "gone"?
- **D6** - Hand-run merge scripts: keep or delete the five archived ones; may the active ones run on S3
  or only on local files?
- **D7** - Several sources per document (held PDF, NARA copy, archive.org): a `sources` list per
  bibliography record, and the rule for which source wins.
- **D8** - Unit facts Grok gives from model memory (no source): keep them flagged unsourced, or drop
  them?
- **D9** - Facts from a URL that cannot be cited: drop them, or keep them flagged?

### Bibliography and NARA
- **D10** - Copyright rule: move the cutoff year with the current year; fix the off-by-one in "death +
  70 years". Confirm the rule before the change.
- **D11** - A NARA catalog entry with no scanned pages: still "resolved", or a new status
  (`reference_identified`)?
- **D12** - Store `archive_reference_number` as the bare record-group number? (Changes stored data.)
- **D13** - Search order record group -> NARA -> archive.org -> Gutenberg -> OpenSERP -> Grok (your
  order; more OpenSERP and Grok calls).
- **D14** - Check every positive find against the page content (one more page fetch + one Grok call per
  find)?
- **D15** - NARA holding address per record group: a table only you can supply, or leave it empty?
- **D16** - A Phase 3 run launched by hand while another holds the lock: wait, refuse, or take over?

### Links and maps
- **D17** - Map URLs through human review: what a map review item contains; which map hosts are trusted.
- **D18** - Store the links between pages of the same volume (today only counted)?
- **D19** - Merge the two document-link steps into one?
- **D20** - Follow the links on a Grok-verified page? (New feature; two Grok checks per link.)
- **D21** - The OpenSERP scope reduction design (`docs/current/design/OPENSERP_SCOPE_REDUCTION.md`).

### Wire up or retire
- **D22** - The SQS OpenSERP path (nothing calls it).
- **D23** - The `map_features` step (no Phase 3 step).
- **D24** - `supplemental_info_pipeline` (nothing calls it).
- **D25** - NOAA weather: supply a token, or retire the step?

### Pipeline and runs
- **D26** - The "dedup complete" event starts Phase 3 for every book with no book named: should it re-run
  paid steps over the whole corpus?
- **D27** - Two tasks overwriting the same shared records: conditional S3 writes, or one writer at a time?
- **D28** - How long Phase 3 waits for the bibliography merge (300 s today)?
- **D29** - Tiered link-safety check: confirm each open blocklist's licence first.

### Spend or access approvals
- **D30** - The Group 0 baseline run, then a test run per group (Grok spend).
- **D31** - Turn on Grok batching for Phase 3 after a validation run (changes spend; adds per-request
  identity checks).
- **D32** - Re-run equipment vision under the "any photograph of the model" rule (Grok spend).
- **D33** - Geocode miss-recovery: native-name retry, OpenSERP rescue, photo GPS (spend).
- **D34** - Re-check the 5,621 older cached Grok answers (reads the xAI key; retrieval is free).
- **D35** - Spot-loss and failover tests on dev (deploy + spend).
- **D36** - A GitHub OIDC role for deploy-on-merge.

## Waiting on other work
- The Group 6 place-hierarchy items (incl. Pacific -> Asia): wait on the Phase 2 Wikidata fix (Track P,
  "Places: the hierarchy from the Phase 2 Wikipedia/Grokipedia lookup is guessed").
- All UI work: deferred to the end (owner 2026-11-24).
