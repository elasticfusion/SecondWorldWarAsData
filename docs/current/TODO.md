# Pipeline Backlog

**Last Updated:** 2026-10-11 (reprioritized index refreshed after the data-quality program + doc audit)

---

## Current Priority (reprioritized index)
**Reprioritized 2026-10-11** (after the data-quality program + documentation audit landed).
Ranking reflects the project goal: *finish unattended ETO ingestion, then build RAG/search*.
Most of the 2026-10-09 data-quality program is now DONE (see "Completed data-quality program" at the
bottom); what remains is ranked here. Current ordered priority:

> **LONG-TERM GOAL · end-to-end observability on EVERY component (owner, 2026-10-14).** Be able to
> WATCH a document flow through the whole system — from "a new document is added" → Phase 0 ingest/
> media-detect → Phase 1 parse → Phase 2 each LLM extraction (events/dates/places/people/groups/
> equipment/supplemental) → dedup → Phase 3 each enrichment (geocode/openserp/nara/weather) → feature
> JSON outputs — and see exactly what each stage received and emitted (incl. the LLM prompt/response
> shape + counts). Approach: a consistent structured log convention (the `OBSERV <stage>.<event> ...`
> lines started in the bibliography chain — events.refs / supplemental.input / supplemental.extract /
> bib.resolved) carrying a per-document CORRELATION/trace id (book+chapter, and ideally a run id) so a
> single CloudWatch Insights query reconstructs one document's full journey; counts at every
> hand-off (N in → N out, with the drop reason when N shrinks). Build it incrementally stage-by-stage;
> each new feature should emit its OBSERV line. This is the generalization of the ad-hoc tracing added
> while debugging the bibliography→NARA chain (where missing visibility cost several blind reruns).
>
> **OBSERV TRACEABILITY PATTERN (owner, 2026-10-15 — the convention for the above).** Every stage
> emits greppable, structured `OBSERV <stage>.<event> key=value ...` INFO lines, fail-safe (a logging
> error never breaks the call), COUNTS/FINGERPRINTS only (never raw content/PII). To compare the SAME
> logical item across stages/processes, log a FINGERPRINT of each relevant input — `len:sha8` (length
> + first 8 hex of sha256), not the content — so two sides can be diffed to find exactly what diverged.
> Instances so far: `events.refs`, `supplemental.input|extract`, `bib.resolved`, and `cachekey.submit|lookup`
> (grok_client — logs the cache key + per-input fingerprints: cache_type/model/temp/system_prompt/prompt,
> so a submit-time batch key and a re-run lookup key for the same request can be compared to pinpoint
> cache-key divergence). As the end-to-end trace is built, add a per-document CORRELATION id to every
> OBSERV line so one CloudWatch Insights query reconstructs a document's whole journey.

1. **~~[HIGH · cost/correctness] Idle-infra teardown backstop reaper~~ ✅ DONE 2026-10-11** —
   (a) root cause fixed: `openserp_manager` now excludes OpenSERP's OWN tasks from the active-work
   count (an idle openserp task no longer blocks its own teardown); (b) new HOURLY
   `idle_backstop_reaper` Lambda — two-strike no-progress watchdog (S3 output-object-count
   progress signal; force-teardown + SNS alert only after 2 consecutive no-progress hours with NAT
   up + no lock/lease; fail-safe). CFN + schedule + update_lambdas wired; code-complete, NOT yet
   deployed. Code-reviewed; full gate PASS. (§ "Fix idle-infra teardown + hourly backstop reaper".)
2. **~~[HIGH · parser/bibliography PREREQUISITE] Capture endnote/footnote CITATION TEXT~~ ✅ DONE
   2026-10-14 (PR #381).** `extract_footnotes` now pairs the in-text `#fnN` marker with its
   end-of-chapter `#cnN` definition and returns `(number, url, text)`; `Footnote.text` added;
   serializers (phase1_parse ×2 + phase1_handler) carry it; `fetch_endnote_texts` PREFERS the
   parsed `footnotes[].text` and skips the fragile ibiblio HTML re-fetch. VERIFIED LIVE on a clean
   RoerDams rerun: all 4 ch2 footnotes now carry real citation text in `-parsed.json`/`-endnotes.json`
   (was `{number,url}` only). HOWEVER this unblocked only the first step — see 2b (the NEW blocker).
   - **[MED · security/architecture — centralize + make secrets dynamic, owner Q 2026-10-15.]** API
     keys are loaded STATICALLY AT STARTUP into `os.environ` and scattered across 7 sites (ecs_entrypoint
     `_load_secrets` for GROK_API_KEY + the new NARA_API_KEY, plus duplicated `get_secret_value` in
     lambda_handlers phase2/phase3/batch_poller/trigger/nat_manager). The NARA load I added deliberately
     MATCHED this existing pattern (consistency > divergence). But a startup-cached key in env for a
     long-lived task (batches up to 24h) is: (a) rotation-unsafe (a mid-run rotation makes the cached
     key stale → failure), (b) higher-exposure (readable by subprocesses/crash dumps/env for the whole
     run). DIRECTION: introduce ONE centralized accessor `secrets.get(name)` with a short-TTL cache +
     rotation-awareness (re-fetch on auth failure), out of the ambient env, and replace ALL 7 scattered
     loads uniformly — Grok, NARA, NOAA, future keys. Do it as one cross-cutting refactor, NOT per-key
     (per-key dynamic for NARA only would be exactly the divergent dual-logic to avoid). Weigh SM call
     cost/latency/throttling + NAT/VPC-endpoint egress for the fetch. Until then, startup-load stands.
   - **[RADON COMPLEXITY TRIAGE — all bibliography-path C+ blocks, owner directive 2026-10-15.]**
     Reviewed every radon C-grade-or-worse function across bibliography code; simplified accidental
     complexity (behavior-identical, gate 2189, PR #407), listed the inherently-complex below.
     SIMPLIFIED (before→after): `events._populate_footnote_refs` C(38)→B(7); `extraction/
     bibliography.extract_bibliography_deterministic` C(20)→A(3) (my new code — note: its C-warning
     was HIDDEN by my own grep filter on gate output; the gate DID surface it); `config._validate_config`
     C(16)→A(4); `supplemental._normalize_citation` C(11)→B(6); `supplemental_search.
     _search_archive_org_api` C(15)→A(5). INHERENTLY COMPLEX — leave as-is (essential logic; splitting
     scatters an invariant or hurts readability): `entity_store.DynamoEntityStore.store_bibliography`
     C(12) — optimistic-CAS cross-host race safety (DO NOT refactor); `events._apply_deterministic_ids`
     C(11) — ID-stability invariant; `events.extract_events` C(18) + `_reconstruct_fulltext` C(11) —
     retry/normalize/auto-split state machine; `bibliography_resolver._search_online_sources` C(20),
     `_search_openserp_archive` C(17), `_search_nara` C(12), `resolve_bibliography_dir` C(13),
     `resolve_bibliography_entry` C(12) — resolver hot path, MED-risk (OpenSERP global-disable + cache
     keys, NARA rate-limit state); a staged simplification (extract `_try_nara`/`_try_openserp` query
     builders) is possible but deferred to its own reviewed change; `phase3_enrich_data.main` E(32) +
     `phase2.main` D(24) + `_run_core_extraction` C(19) — sequential phase orchestration (top-level
     scripts, OUTSIDE the gate's radon scope); `referential_integrity.audit` C(19) — single-read
     O(records) design (nesting IS the optimization); dedup_ui_handler `_merge_generic_files`/
     `_rename_to_match_content`/`_handle_action`/`handler` — HIGH-risk dual local+Dynamo write path, do
     NOT refactor casually (also a LATENT BUG flagged: `_rename_to_match_content` has
     `name.lower().replace(" ", " ")` — a space→space no-op, likely meant `"_"`; preserve behavior,
     fix separately). `extract_supplemental` E(33) — simplifiable by lifting the 2 OBSERV telemetry
     blocks + endnote-url-map into helpers (MED, on the batch/cache path), deferred.
   - **[HIGH · architecture — owner directive 2026-10-15] Promote footnote/endnote extraction to a
     DETERMINISTIC PRIMARY pass (not a secondary batch step).** The entire 0-bibliography fragility
     comes from bibliography being a SECONDARY optional-batch extractor that depends on the
     event→supplemental→batch→cache chain (volatile prompt key, submission-order mapping, cross-process
     cache visibility, batch completeness — every failure this session). But the citation CONTENT is
     ALREADY captured deterministically in Phase 1: `src.parser.extract_footnotes` returns
     `(number, url, text)` and persists it in `*-parsed.json` (verified on real RoerDams data:
     6/6 footnotes with real text, e.g. `1 → "See Interv with Collins, 25 Jan 54."`, `need_fetch=0`).
     DIRECTIVE: make a PRIMARY deterministic pass that walks the parsed footnotes/endnotes and writes a
     bibliography-candidate record per citation IMMEDIATELY — `verbatim_reference` = parsed text,
     `reference_type` (footnote/endnote), source `resource_urls`, chapter/event linkage — with NO LLM,
     NO batch, NO cache. The LLM classification/structuring (document_reference vs factual vs ambiguous;
     author/title/publisher parsing) then runs as an ENRICHMENT OVERLAY on records that already exist,
     not as their gatekeeper. Benefits: (a) bibliography existence no longer depends on the batch/cache
     at all — the record exists the moment Phase 1 parses the citation; (b) graceful degradation by
     construction (this SUBSUMES the retrieve-only live-fallback item / PR #403 — a missed/failed LLM
     classification leaves the deterministic record intact with full verbatim provenance); (c) aligns
     with the deterministic-first / LLM-as-enrichment architecture principle. Wire: new primary step in
     phase2 (or late phase1) emitting to `output/bibliography/` via `store_bibliography_entry`
     (schema `src/schemas/bibliography_output.py`: BibliographyID+title required, reference_type,
     verbatim_reference, resource_urls all present); supplemental's `_route_by_content_class` becomes an
     update-in-place classifier on those records.
   - **[PHASE 2 & 3 FIELD OWNERSHIP + the resolver short-circuit interaction, owner review 2026-10-15.]**
     Field population plan for the deterministic bibliography records (which currently carry only
     verbatim_reference + resource_urls + public_domain license):
     • `license`/`copyright_status` → ✅ DONE deterministically in Phase 2 (ibiblio/hyperwar = US-Gov
       public domain, from the source domain).
     • `citation.title`/`alt_title`/`author`/`publisher`/`document_type`/… → PHASE 2 LLM OVERLAY. The
       supplemental prompt already extracts these (prompts/supplemental.yaml: title/alt_title with the
       edited-volume rule book-title→title, chapter-title→alt_title). The overlay must UPDATE the
       deterministic record in place (see the remove-divergent-logic item) rather than create a
       parallel record, and parse them from `verbatim_reference`.
     • `availability`/real `resource_urls`/`archive_reference_number` → PHASE 3 RESOLUTION (NARA /
       archive.org / LOC). NARA key IS wired (NARA_API_KEY→api.nara_api_key→resolver), but config.yaml
       has it empty — load the `dev-wwii-pipeline/nara-api-key` secret for a real NARA attempt.
     ❗ CRITICAL INTERACTION (blocks the ultimate NARA goal): `resolve_bibliography_entry`
     (bibliography_resolver.py:117) SHORT-CIRCUITS to `search_status=resolved` whenever
     `entry.resource_urls` is non-empty — AND the deterministic pass puts the ibiblio `#fnN` SOURCE
     page URL into top-level `resource_urls`. So all 16 records would be marked "resolved" and Phase 3
     would NEVER search NARA. But the ibiblio `#fnN` link is the PROVENANCE page where the citation
     TEXT appeared, NOT a resolvable copy of the CITED document ("MS # B-810, Schmidt, Kaempfe im
     Rheinland" is a NARA/archival doc). FIX (source-URL vs resource-URL, a distinction the prompt
     already names): the deterministic pass should put the ibiblio source URL on the MENTION as
     provenance (e.g. mention.source_url) and leave top-level `resource_urls` EMPTY, so Phase 3
     resolution runs and fills `resource_urls` with the real NARA/archive locator. (Alternatively, the
     resolver short-circuit must not treat a provenance/source URL as a resolution.) This is the next
     change needed before a real NARA resolution is possible.
   - **[HIGH · DURABILITY BUG — deterministic bibliography is Dynamo-first, but S3 is the SYSTEM OF
     RECORD. owner directive 2026-10-15.]** ARCHITECTURE (owner, authoritative): **S3 is the definitive,
     durable store; DynamoDB is a DERIVED, rebuildable, cost-incurring working index** — S3 can
     rebuild Dynamo, not vice-versa, and Dynamo is more expensive. So the write dependency must be
     S3-authoritative, Dynamo-derived. CURRENT (wrong) behavior, PROVEN in the e2e: in AWS mode
     `store_bibliography_entry` → `DynamoEntityStore`, so the deterministic pass wrote the 16 RoerDams
     records to the Dynamo cache (`entity#bibliography#…` in dev-wwii-api-cache) and the Phase-2 submit
     task logged `Skipping bibliography/supplemental bulk download (Dynamo-backed dedup)` — leaving
     `s3://…/output/bibliography/` EMPTY (0 files). The Dynamo→S3 materialization only runs later in the
     DEDUP phase (`_materialize_from_dynamo`, ecs_entrypoint:931/1849). So if the batch/retrieve/dedup
     chain stalls or is interrupted (as it did repeatedly this session), the records NEVER reach the
     durable store — exactly the fragility the deterministic-primary pass was meant to ELIMINATE. I had
     to materialize+`aws s3 cp` the 16 records to S3 BY HAND to make them durable (verified: 16
     well-formed `output/bibliography/*.json` with verbatim_reference + resource_urls + mention
     provenance). FIX: the deterministic pass (and the bibliography write path generally) must persist
     to S3 as the AUTHORITATIVE write within Phase 2 — either write `output/bibliography/*.json` locally
     + background/final-sync in the submit task (so it's durable the moment Phase 1 parse + the primary
     pass complete, no dedup/batch dependency), with the Dynamo index written as a DERIVED copy; or run
     `_materialize_from_dynamo`+sync for bibliography at end of the submit pass. Also reconsider the
     "Skipping bibliography bulk download (Dynamo-backed dedup)" optimization under the S3-authoritative
     model. This is the last correctness gap before bibliography is truly robust.
     ✅ BRIDGE IMPLEMENTED 2026-10-15: `materialize_bibliography_to_local(output_root)` (bibliography.py)
     mirrors the Dynamo-backed entries to local `output/bibliography/*.json`, called in phase2 right
     after the deterministic pass, so the Phase-2 background/final sync persists them to S3 within the
     submit pass (no dedup/batch dependency). Forward-compatible with Dynamo deprecation: when the
     entity store is removed, `get_entity_store()` returns None → the helper is a no-op and the
     file-based `store_bibliography_entry` path already writes `output/bibliography/*.json` directly.
   - **[DIRECTION · owner 2026-10-15: DynamoDB will likely be DEPRECATED ENTIRELY from JSON storage.]**
     S3 is the definitive, durable, cheaper store and can rebuild Dynamo; Dynamo is a derived index and
     costs more. PLAN accordingly: do NOT deepen the Dynamo write/dedup path. The reason Dynamo was
     introduced for bibliography (store_bibliography_entry docstring) was concurrency-safe cross-Fargate
     writes + avoiding a 13,566-file bulk download — so DEPRECATION REQUIRES first solving those in an
     S3-native way: (a) a concurrency-safe S3 write/dedup scheme for the bibliography dir (e.g.
     per-record immutable files keyed by a deterministic content hash so concurrent books never collide,
     + a periodically-rebuilt index object, instead of a single flock-guarded index.json), and (b) keep
     Phase 2 from bulk-downloading the whole bib dir (scoped/manifest reads). Until then, the file path
     + the materialize bridge above keep S3 authoritative. When ready, remove DynamoEntityStore from the
     JSON entity path entirely (people/places/… too, not just bibliography) and derive any needed index
     from S3.
   - **[HIGH · remove divergent logic — follow-up to the deterministic pass, owner Q 2026-10-15].** The
     deterministic pass now CREATES bibliography records; the old supplemental `document_reference`
     branch in `_route_by_content_class` STILL calls `store_bibliography_entry` to create records too.
     MEASURED convergence/divergence (local repro): when the LLM did NOT parse a real title (title stays
     "Unknown"), the supplemental record shares the verbatim dedup key and CONVERGES into the
     deterministic record (1 file — safe). But when the LLM parses a REAL distinct title, dedup keys on
     the parsed title, so it creates a SECOND file = a DUPLICATE of the same citation (deterministic
     "Unknown" + enriched real-title). This is the previous-logic to remove: supplemental's
     document_reference branch must UPDATE THE DETERMINISTIC RECORD IN PLACE (enrichment overlay —
     fill title/author/classification on the existing record), NOT create a parallel one. Mechanism
     (REVISED for Dynamo deprecation — do NOT invest in the Dynamo index): put the convergence in the
     S3/FILE layer — a stable `verbatim` match-key in the FILE-based bibliography index so a record
     created with title=verbatim and later enriched with a real title resolves to the SAME file entry;
     then make the supplemental branch pass the deterministic record's identity (or match by verbatim)
     and merge fields instead of inserting. Scoped as its OWN change (touches the shared dedup across
     local + Dynamo stores) with its own review — NOT folded into the additive deterministic-pass PR,
     where the Unknown-title case already converges safely.
     ↳ **[MED · data-quality, surfaced by the PR #405 re-review of the merged code].** The
     deterministic path attaches NO EventID/Sub-eventID, so `_has_mention` dedups on
     `reference_number` alone WITHIN a matched bib entry. Two DIFFERENT chapters citing the same
     source and reusing the same footnote number (both "fn 1") match the same entry on verbatim and
     then collapse on reference_number="1" → the 2nd chapter's mention is silently dropped (latent
     cross-chapter mention-loss, not a crash; re-running the SAME chapter is already safe — verified by
     test_idempotent). FIX: include `book`+`chapter` in the mention identity so a mention is unique per
     (book, chapter, reference_number) when EventID is absent; add a test for the cross-chapter
     same-number case. Fold into the same enrichment-overlay change above (both touch mention/dedup
     identity).
     ✅ FOOTNOTE/ENDNOTE source IMPLEMENTED 2026-10-15: `extract_bibliography_deterministic`
     (src/extraction/bibliography.py) reads parsed `footnotes`/`endnotes` and emits one record per
     citation (verbatim_reference + resource_urls), wired as a PRIMARY step in phase2 `_process_event`
     (runs regardless of batch_mode; idempotent; skips ibid/op.cit/see-n. stubs). Proven: 16 records
     from RoerDams with zero LLM/batch/cache.
   - **[HIGH · bibliography — owner Q 2026-10-15: capture the ACTUAL bibliography section + abbreviations
     decoder, not just footnotes/endnotes].** These CMH green books (verified against the real source
     ToC, ibiblio hyperwar USA-E-Siegfried) have BACK-MATTER the pipeline does NOT yet capture: a
     **"Bibliographical Note"/"Note on Sources"** (full, already-resolved source citations — a BETTER
     NARA-resolution input than cryptic footnotes) and a **"List of Abbreviations"/Code Names** that
     DECODES the footnote shorthand (FUSA=First U.S. Army, AAR=After Action Report, "A Gp", "G-2 Per
     Rpt", MS #B-810, …). The parser has NO detector for these sections today (grep: no
     bibliography/works-cited/abbreviations section handling in src/parser.py or src/ingestion), and the
     current RoerDams test material is only 3 content chapters (no back-matter), so this is untested.
     DESIGN: deterministic bibliography should ingest from THREE deterministic sources in quality order
     — (1) the actual Bibliography/"Bibliographical Note" section (full citations) → highest-quality
     records; (2) footnotes/endnotes (DONE — complete coverage but cryptic); (3) the Abbreviations list
     as a DECODER applied to #2's verbatim text to EXPAND shorthand before NARA resolution (e.g.
     "12th A Gp and FUSA G-1 Daily Summaries" → "12th Army Group and First U.S. Army G-1 Daily
     Summaries"). WORK: add a parser back-matter section-detector (titles/first-para match on
     "bibliograph*", "works cited", "note on sources", "abbreviations", "code names"); emit
     `*-bibliography.json` / `*-abbreviations.json` under the book output; have
     `extract_bibliography_deterministic` prefer the bibliography-section entries and apply the
     abbreviations map to footnote verbatims. The LLM/NARA resolution then runs on richer, decoded
     citations.
   - **[CONTEXT — the 5 SECONDARY (second-batch-cycle) extractors, owner Q 2026-10-15.]** The Phase-2
     optional pass `_extract_optional_entities` (phase2_extract.py:146–150) runs FIVE secondary
     batch-collected extractors sharing the same fragile submit→batch→retrieve-cache pattern:
     **weather, equipment, logistics, casualties, supplemental_material(→bibliography).** All five are
     subject to the cache-key / `__contains__` / batch-mapping risks (now fixed PR #401/#402/#393/#395).
     Only SUPPLEMENTAL manifested as total-silent-failure because it is terminal-ROUTED with no own
     entity file (the others write weather/equipment/… files and degrade to a re-extraction on a miss).
     Of the five, ONLY bibliography can be promoted to deterministic-primary (above) because only its
     source content (citation text) is already parsed deterministically — weather needs an API,
     equipment/logistics/casualties need genuine LLM reading of narrative, so those legitimately stay
     secondary. Phase 3's secondary SEARCHES (OpenSERP / NARA / archive.org / geocode / NOAA·Open-Meteo)
     are a DIFFERENT, more-resilient class: enrichment lookups keyed on already-persisted entities, not
     batch-cache-dependent extraction.
   - **[EMPIRICAL CORRECTION — "why do the others work but bibliography doesn't?" owner Q 2026-10-15.]**
     The premise is partly an ILLUSION. Real RoerDams run output (the 165-file mirror) was:
     casualties=13–16, equipment=1, **weather=0, logistics=0**, supplemental=0, bibliography=0. So
     bibliography is NOT uniquely failing — weather and logistics are ALSO at zero. Why it LOOKED
     unique: (a) it has a LOUD downstream consumer (Phase 3 NARA resolution logs "Bibliography
     directory not found"), (b) we were actively driving/watching it. Why casualties/equipment DO
     produce files while supplemental doesn't, despite identical batch/cache plumbing: their output
     IS the raw LLM response written directly to their own entity file — a cache hit becomes a file
     with no further gates. Supplemental is TERMINAL-ROUTED: after the cache hit, the response must be
     classified as `document_reference` AND then routed to a SEPARATE `store_bibliography_entry` — two
     extra post-cache failure points. weather/logistics at 0 are indistinguishable between "legitimately
     empty for these chapters" and "same cache fragility" — exactly the silent-degradation the
     deterministic-primary redesign eliminates for bibliography.
   - **[HIGH · bibliography BLOCKER] Phase 2 still produces ZERO `output/bibliography/` despite
     citation text now present.** Verified 2026-10-14: after PR #381, the RoerDams rerun's Phase 3
     bibliography step logged `Bibliography directory not found: output/bibliography` /
     `0 resolved, 0 not found` — so NARA success rate is still unmeasurable (0 of 0). Traced:
     `phase2_extract._extract_optional_entities` runs `_extract_supplemental` PER event file IN-TASK
     (events written to `output/content/{book}/*-event.json`, processed before the entity-only
     sync), and `extract_supplemental` → `fetch_endnote_texts(parsed)` now DOES return the citation
     text (confirmed in a local repro against the real RoerDams parsed file). Yet 0 bibliography and
     NO "Supplemental" log lines appeared. CANDIDATE CAUSES to check (in order): (i) STALE
     `output/supplemental/.processed_events.json` marker surviving the e2e_reset wipe → `_is_processed`
     makes `_extract_supplemental` early-return silently for every event (strongest candidate — the
     marker was present in S3; e2e_reset should also clear `.processed_events.json` under
     output/{supplemental,casualties,logistics,maps}); (ii) Grok supplemental extraction classified
     nothing as `document_reference` (→ nothing routed to `store_bibliography_entry`); (iii) events
     lacked `Endnote_References`/`Footnote_References` so `create_supplemental_prompt` built no
     prompts. FIX: confirm which, then make e2e_reset clear the supplemental processed-markers and/or
     fix the classification. This is now THE blocker for any NARA measurement.
     ✅ CAUSE CONFIRMED 2026-10-14 = (i): `output/supplemental/.processed_events.json` held exactly
     `["chapter14a-event.json","chapter2a-event.json","chapter3a-event.json"]` (stale from the
     pre-wipe run) → `_is_processed` true for all 3 → `_extract_supplemental` early-returned silently
     for every event (hence 0 bibliography + no "Supplemental" log lines). NOT a parser/supplemental
     logic bug — a RESET-HYGIENE gap. FIX: `scripts/e2e_reset.sh` (+ `--wipe-title`) must delete
     `.processed_events.json` under output/{supplemental,casualties,logistics,maps,weather} when
     wiping a title, so a re-ingest re-runs the optional extractors.
     ✅ MARKERS CLEARED + RERUN 2026-10-14: after deleting the stale markers, supplemental DID run
     ("Extracting supplemental material from chapter{2,3,14}a-event.json") and batched 10 supplemental
     requests ({'supplemental':10,'logistics':3,'casualties':1}); the batch completed 14/14 success
     and "Optional batch complete! Re-running with cached results". ❗ YET STILL 0 bibliography.
     So there is a SECOND, deeper blocker (candidate ii now active): in BATCH mode the supplemental
     re-run yields no `document_reference` persisted to `output/bibliography/`. NARROWED next step:
     inspect (a) whether the batch re-run path actually re-invokes `extract_supplemental` →
     `_route_by_content_class` → `store_bibliography_entry` (vs only re-running core entities), and
     (b) an actual Grok supplemental batch response for a RoerDams sub-event (is it classifying the
     citations as document_reference at all, or returning empty?). The parser fix (text capture) and
     the marker-hygiene are confirmed; the remaining gap is the batch-mode supplemental→bibliography
     persistence/classification. (Also fix e2e_reset to clear the markers so this doesn't recur.)
     ✅ BLOCKER B FIXED 2026-10-14 (PR #383): the batch-COLLECT pass swallowed `BatchModeCollecting`
     in `_extract_with_retry` → event got `_mark_processed` during collection → the batch RE-RUN
     skipped it. Fix re-raises `BatchModeCollecting` so collect leaves it unprocessed and the re-run
     reprocesses. VERIFIED on the next rerun: supplemental now FULLY ROUTES on the re-run — logs
     `chapter{2,3,14}a-event.json: 0 bibliography, 0 factual, 0 ambiguous` (was "No supplemental
     material extracted" / early-exit before). So the batch path now works end-to-end.
   - **[HIGH · bibliography BLOCKER C — the current one] Sub-events carry NO `Footnote_References`/
     `Endnote_References`, so supplemental has no footnotes to classify → 0 bibliography.** After B,
     supplemental routes but gets `0 bibliography, 0 factual, 0 ambiguous` for every chapter.
     ROOT CAUSE (verified 2026-10-14): `create_supplemental_prompt` reads
     `sub_event.get("Endnote_References")`/`get("Footnote_References")` to attach the endnote text to
     the Grok prompt — but the EVENTS extractor (`src/extraction/events.py` + its prompt) NEVER
     populates those arrays (grep: no `Footnote_References`/`Endnote_References` produced there). So
     every sub-event has empty refs → `_build_ref_context` yields an empty endnote block → Grok is
     asked to extract supplemental material with NO footnotes → correctly returns 0. The raw
     material IS present and connectable: the parsed paragraph text RETAINS the `[1]/[3]/[18]/[21]`
     markers (verified: 8 marker occurrences in ch2 paragraphs) and `footnotes[]` has all 4 with
     text. FIX: carry the footnote-number references onto each sub-event during event extraction
     (populate `Footnote_References`/`Endnote_References` from the `[N]` markers in the sub-event's
     own fulltext), so `create_supplemental_prompt` can hand Grok the right footnotes → classify
     document_reference → write bibliography → NARA finally has inputs. This is now THE blocker for
     a real NARA success rate.
     ❌ FIRST FIX ATTEMPT (PR #385) INSUFFICIENT — observability (PR #386/#387) proved why
     (2026-10-14): `_populate_footnote_refs` scans each sub-event's `Sub-event_fulltext` for
     `<sup>[N]</sup>` markers, but the OBSERV run showed `events.refs with_refs=0` for EVERY
     sub-event (while `parsed_footnotes>0`, `endnote_texts=4` available), and the decisive sample
     `OBSERV events.refs.sample no-marks: has_sup=False has_bracket=False head='The First U.S. Army
     advanced ~750 miles from St. Lô...'` — i.e. the reconstructed `Sub-event_fulltext` is CLEAN
     LLM-SUMMARIZED PROSE with NO `<sup>` markers at all. The markers live in
     `parsed_data["paragraphs"][].text` (raw), NOT in the summarized fulltext. CORRECT FIX: in
     `_populate_footnote_refs`, derive each sub-event's refs from the PARSED PARAGRAPHS ASSIGNED to
     it — i.e. use the sub-event's `paragraphs` number array (the same one `_reconstruct_fulltext`
     consumes/pops) to look up the raw paragraph text in `para_lookup` and scan THAT for `<sup>[N]`.
     Must run BEFORE `_reconstruct_fulltext` pops `paragraphs`, or capture the mapping first.
     (The downstream chain — supplemental.input→extract→route→bibliography→NARA — is otherwise ready;
     OBSERV confirmed endnote_texts arrive. This is the last code change before a non-zero NARA.)
     ✅ REFS FIXED (PR #390, verified live: every sub-event has Footnote_References from the LLM
     `references` field). ✅ BATCH RESULT MAPPING FIXED (PR #393/#395): the xAI results API omits
     custom_id, so retrieve now maps results to OUR cache keys by submission order via
     BatchMetrics.submitted_request_ids. OFFLINE-VALIDATED against the persisted batch
     (9a96241f...c47504c396f8, valid 9/9): zip(submitted_request_ids, retrieve_results_ordered) maps
     the supplemental results to OUR keys (f7bb3e00.., aa8e8793..) and 2/3 carry real
     Supplemental_Materials with document_reference content → `Populated 9 cache entries` on the
     live retrieve. ❗ STILL 0 BIBLIOGRAPHY — the LIVE retrieve re-run's supplemental logs
     `OBSERV supplemental.extract prompts=N responses=0 materials=0`, i.e. the subprocess
     supplemental's `extract_json` does NOT hit the cache the retrieve just populated. Both parent
     (retrieve) and subprocess use DynamoCacheBackend (aws.enabled) + the same cache_type sub-caches,
     so it is NOT a backend split. REMAINING ROOT CAUSE (next session starting point): a cache-KEY
     mismatch between the SUBMIT-time key (stored as submitted_request_ids = request_id passed to
     BatchRequest at collect time) and the key the RE-RUN computes for the same sub-event. `_make_cache_key`
     = sha256(f"{system_prompt}:{prompt}:{temperature}:{model}"). Suspects to trace with ONE run (OBSERV
     the computed key on both sides): (a) the supplemental PROMPT differs submit-vs-rerun (e.g. endnote
     text block ordering / sub-event fulltext reconstructed differently), (b) model string differs
     (_get_model(cache_type) vs what was stored), (c) system_prompt differs.
     ✅ TRACED 2026-10-15 via the new `OBSERV cachekey.submit|lookup` (PR #399): comparing submit-pass
     vs retrieve-rerun for supplemental — model=grok-4.3, temp=0.1, sys=64:f02baf8b are IDENTICAL on
     both sides; the **prompt fingerprint DIFFERS** (e.g. submit prompt=2205:e1643790 vs lookup
     prompt=2040:1235bd03; different length AND sha). So the SUPPLEMENTAL PROMPT is NON-DETERMINISTIC
     across the two phase-2 invocations → different `_make_cache_key` → cache miss → responses=0 →
     0 bibliography. (A couple of prompts DID match — b5039f5a/1cb63f2b appear on both sides — so it's
     partial, pointing to per-sub-event non-determinism, not a global difference.) ROOT CAUSE to fix
     next: make `create_supplemental_prompt`'s inputs deterministic across runs — prime suspects are
     `event_id`/`sub_event_id` (if `_apply_deterministic_ids` yields different ULIDs submit-vs-rerun,
     or the submit pass used the LLM's raw IDs while the rerun used deterministic ones) and the
     `text` fulltext reconstruction (dict value ordering / paragraph_numbers). Either make the prompt
     INDEPENDENT of volatile ids (don't embed event_id/sub_event_id in the cached prompt), or ensure
     ids + fulltext are byte-identical across invocations. The submission-order MAPPING (#393/#395) is
     proven correct; THIS prompt-determinism fix is the final step to a non-zero bibliography.
     ✅ RESOLVED 2026-10-15 by THREE fixes (keys now match live + cache populated + visible):
     (1) PR #401 stable supplemental cache key (decoupled from the volatile fulltext — ids+refs only);
     (2) PR #393/#395 submission-order result mapping; (3) PR #402 the ACTUAL final layer —
     `DynamoCacheBackend.__contains__` was treating the preloaded in-memory snapshot as authoritative
     for ABSENCE, so a cache entry written by the PARENT (batch populate) was invisible to the retrieve
     SUBPROCESS's `key in cache` test (its preload predated the write). Verified the entry existed in
     Dynamo under `books/RoerDams/supplemental#` with real `Supplemental_Materials` while the lookup
     reported `responses=0`. __contains__ now falls through to Dynamo on a snapshot miss. This was a
     GENERAL cross-process cache-correctness bug; it manifested only for bibliography because
     supplemental is the one extractor whose output is terminal-ROUTED with no live fallback AND had a
     non-deterministic key — a miss there wrote NO file, whereas equipment/people silently re-extract
     on a miss and still write a record.
   - **[HIGH · robustness] Supplemental retrieve-only LIVE FALLBACK (graceful degradation).**
     Even with the cache fixes above, a GENUINELY missing batch answer for a supplemental sub-event
     (batch partially failed; a request returned `finish_reason != "stop"`; or
     `_map_batch_results_by_index` count-mismatch → `{}`) still writes NO bibliography for that
     sub-event on the retrieve re-run (`SKIP_RETRY=1`, `batch_mode=False`) — silent total loss, the
     same failure class as the original 0-bibliography bug. Equipment/people tolerate the same miss
     because their output degrades to a re-extraction that still writes a record. FIX: on the
     RETRIEVE RE-RUN ONLY (never in the batch-COLLECT pass — a live call there would re-break Blocker B
     / PR #383, which deliberately re-raises BatchModeCollecting so collection leaves the event
     unprocessed), if a sub-event HAS refs but got no cached batch answer, make a single bounded live
     Grok call for just that sub-event and route it. Gate the fallback on `SKIP_RETRY`/`batch_mode is
     False` + presence of refs; log an OBSERV `supplemental.fallback` so it's observable. Makes
     bibliography degrade gracefully like the other features instead of failing silent-and-total.
   - **[HIGH · infra reliability] NAT-manager orphaned-lock deadlock.** `_create_networking`
     acquires `lock#nat-manager` with ONLY `attribute_not_exists` (no stale-takeover); if a holder
     times out/crashes without the `delete_item` release (observed: a 300s Lambda timeout), the lock
     orphans past its own `ttl` until DynamoDB's sweeper lags, and EVERY subsequent invocation logs
     "Another instance running, waiting..." and times out → NO NAT ever provisions → Phase 2/3 stuck
     with a lease but no egress. Had to manually delete the stale `lock#nat-manager` to recover the
     2026-10-14 RoerDams run. FIX: stale-lock takeover on acquire (reclaim when the stored
     timestamp/ttl is expired), mirroring the url_registry observed-bytes optimistic-lock pattern.
   - **[HIGH · infra reliability] Batch-poller retrieve-claim wedge (same lock class).**
     `batch_poller._trigger_retrieve` does `_claim_retrieve(batch_id)` (writes `retrieve#<id>`) and
     THEN synchronously invokes nat_manager + `_wait_for_nat()` (up to ~2min) before `run_task`. If
     the poller Lambda hits its 5-min timeout while waiting for NAT (observed 2026-10-15: a 300000ms
     TIMEOUT invocation), the claim is left HELD with NO retrieve task launched, and every later poll
     logs "Retrieve already claimed, skipping" → the completed batch is PERMANENTLY wedged at
     `ready` (bibliography/entities never produced) until the `retrieve#` claim is manually deleted.
     In unattended batch mode this silently strands batches that Grok already completed. FIX (two
     parts): (1) give the `retrieve#` claim a TTL / release-on-failure (stale-takeover) so a dead
     launcher can't wedge the batch; (2) either claim AFTER a successful `run_task` (not before), or
     drop the long synchronous `_wait_for_nat()` in the poller and rely on the ECS task's own
     `_ensure_nat_if_needed` readiness gate. Same lesson as the NAT-manager lock: never hold a
     coordination lock across a slow/failure-prone op without TTL/takeover. (Workaround until fixed:
     delete the stale `retrieve#<batch_id>` key, or launch the `--retrieve-only` task directly.)
3. **[HIGH · bibliography enrichment] Resolve + DOWNLOAD bibliographic sources.** Extends
   `bibliography_resolver` (spec: docs/current/dataquality/bibliography_resolution_process.md).
   Beyond the existing find-URL behavior: (a) CHECK EXISTING holdings/`resource_urls`/
   `archive_reference_number` before any search (partly done — strengthen); if a NARA record is
   already present, do NOT re-search. (b) NARA for primary/military records (reference usually
   physical-only — record the RG/location even with no digital copy); Archive.org for non-NARA
   (books, articles). (c) When a copy IS downloadable, DOWNLOAD it for further processing with
   format preference EPUB > PDF > text; gate downloads on copyright_status (public-domain books
   download; non-PD note-only). (d) If no download available, record a distinct
   "available-on-visit / queued-for-future-inclusion" status (not plain not_found). Decide
   download destination (new `sources/` prefix vs `contentrepository/` for re-ingestion) + whether
   a downloaded PD book feeds back into Phase 0/1. Depends on #2 (no citations => nothing to
   resolve/download). Also wire the NARA + NOAA secrets into runtime config (secrets exist:
   `dev-wwii-pipeline/{nara,noaa}-api-key`; `_load_secrets` loads only Grok; `config.py` maps
   NARA_API_KEY->api.nara_api_key but has NO NOAA mapping; add both + load both for Phase 3).
   NOTE: NOAA key is stored at Secrets Manager `dev-wwii-pipeline/noaa-api-key`.
   - **[HIGH · bibliography schema/provenance] A document must support MULTIPLE DISTINCT SOURCE
     ENTRIES, each with independent provenance / format / license (owner, 2026-10-14).** Today the
     bibliography schema (`src/schemas/bibliography_output.py`) flattens this: `resource_urls` is a
     bare `array[string]` (no per-URL metadata) and `search_source` is a SINGLE string — so a work
     resolved at e.g. NARA (scan, gov-PD) AND archive.org (EPUB) AND a modern reprint (PDF,
     in-copyright) can list the URLs but CANNOT record which source gave which URL, nor each copy's
     format/license/access independently. FIX: replace `resource_urls: [string]` with a
     `sources: [object]` array (keep `resource_urls` as a derived/compat view or deprecate), each
     entry carrying its OWN: `url`, `source`
     (nara|nara_physical|archive_org|hathitrust|gutenberg|loc|reprint|openserp|...),
     `format` (scan|pdf|epub|text|djvu), `license` + `copyright_status` (per-COPY, since a PD
     original scan and an in-copyright reprint of the same title differ), `access`
     (free|paywalled|physical_only), `archive_reference_number`/`archive_physical_address`
     (per-source), `download_status`/`download_path` (per-source), and `source_qualification` MAY
     stay document-level or move per-source if copies differ in evidentiary weight. Keep it additive
     + nullable; bump `bibliography` in `ENTITY_SCHEMA_VERSIONS` + re-pin
     `tests/test_schema_fingerprint.py` (same pattern as groups unit_patch). The resolver
     (`bibliography_resolver`) + the OBSERV `bib.resolved` line should then record EACH source, and
     the download step (#3) gate per-source on that copy's copyright_status. Pairs with the artifact
     store (ARTIFACT_STORE_DESIGN.md) — each stored copy is one of these source entries.
     **CAPTURE HERE (owner, 2026-10-14): a NARA source entry is API-authoritative only.** Grok is
     NOT reliable for NARA records — `_identify_nara_record` currently asks Grok for the Record
     Group and trusts a reply containing "RG", which yields plausible-but-wrong/fabricated RG/NAID
     (worse than not_found for a citable corpus; violates the trust model). As part of this
     multi-repo change, a `source=nara` entry may ONLY be created from a REAL
     `catalog.archives.gov/api/v2` hit (`_search_nara`) — store the catalog-returned NAID + URL.
     Grok may only help FORM the search query; drop/neuter the Grok-identify path so a Grok "RG…"
     answer can never become a stored reference. Same rule generalizes to any authoritative-registry
     source (NARA/LOC/ISBN): API-grounded existence + ID, Grok limited to query shaping.
     **EVENTUALLY (owner, 2026-10-14): OpenSERP as a discovery source too.** Beyond NARA/archive.org,
     use OpenSERP web search to FIND candidate copies/pages for a citation; each relevance-verified
     hit becomes a `sources[]` entry (`source=openserp` or the resolved host), fetched through the
     existing SSRF-safe `url_fetch.fetch()` + `url_content_verdict.evaluate()` + URL-registry dedup
     (the central solution already wired for Phase-3 web results) — NOT trusting Grok to assert the
     URL. Unlike the registry/NARA sources, an OpenSERP-discovered copy is lower-qualification (bare
     web) and should carry that in its per-source `source_qualification`.
     **ALSO (owner, 2026-10-14): direct archive.org search is a first-class repo source.** Query
     the archive.org API directly (`advancedsearch`/metadata) for a copy — a hit becomes a
     `source=archive_org` entry with its item URL + format (scan/pdf/epub/txt) + the item's license.
     **RESOLUTION PRECEDENCE (owner): try ONLINE repos first, physical NARA visit as LAST RESORT.**
     Order per citation: (1) already-held local holding; (2) online repos — archive.org direct,
     HathiTrust/LOC/Gutenberg, NARA catalog API (digital copy), OpenSERP-discovered web copy; (3)
     if NO online copy exists, emit a `source=nara_physical` (or `archive`) entry with
     `access=physical_only` + `availability=offline` recording EVERYTHING NARA staff need to pull it
     in person: Record Group, series/entry, box/folder, microfilm roll, and finding-aid reference
     (from the catalog API, never Grok-guessed). That physical-visit entry is a legitimate
     `resolved`-with-location outcome (not `not_found`) — "available on visit", queued for future
     retrieval. Each tier is one `sources[]` object; a document may have several (online copy AND a
     physical NARA locator).
4. **[HIGH · data-quality/provenance] URL-sourced enrichment + 90-day URL registry.** Design:
   docs/current/dataquality/URL_SOURCED_ENRICHMENT_DESIGN.md. Phase 3 step 1 (people_groups,
   enrich_groups.py) and step 2 (places, enrich_places.py `_try_grok`) currently accept Grok's
   GENERATED facts directly with NO source URL + NO verification. Change the trust model: Grok
   facts are UNTRUSTED; Grok must CITE source URLs; each URL is queued + Grok EVALUATES its
   relevance (correct unit/place + WWII era), fail-closed; URL-sourced (Grok-evaluated) data gets
   SOME trust + provenance. Persist examined URLs + verdicts in DynamoDB (`dev-wwii-api-cache`,
   key `url#{sha256}`, native TTL = 90 days — owner-confirmed) so subsequent Grok/OpenSERP
   searches REUSE within 90 days and re-examine after. Reuse should_re_search / the 90-day
   openserp_searched gate / _URL_FETCH_RETENTION_DAYS rather than duplicating. OPEN: fetch-then-
   evaluate (recommended — Grok-claimed URLs can be hallucinated, cf. the 48%-bad-URL 2026-06-05
   bibliography reset) vs judge-URL-string; places same change vs follow-up; drop vs flag
   (`source: grok_unverified`) facts with no cited URL. people_groups first.
   DONE 2026-10-13 (modules; wiring remains): `src/utils/url_hygiene.py` (clean_url: https-only,
   SSRF-safe, normalize + option-2 query = drop args after `?` EXCEPT identity allowlist
   id/q/page/naid/... so distinct NARA/viewer docs stay distinct) + `src/utils/url_registry.py`
   (DynamoDB `url#{sha256(clean_url)}`, 90-day TTL, register/lookup/should_examine, REJECTS
   non-clean URLs). 13 tests, gated; JSON linter added to gate+CI. DECISIONS: (1) fetch-then-
   evaluate the URL content [resolved]; (2) people_groups AND places same change [resolved];
   (3) OPEN: drop vs flag grok_unverified for no-URL facts; (4) DEFERRED: leave per-entity date
   markers. CENTRALIZED (owner): the registry is SHARED code used by BOTH Phase 2 (Grokipedia/
   Wikipedia) AND Phase 3 (OpenSERP + Grok-cited) to stop repeated lookups pipeline-wide —
   complementary to the query-keyed search_cache (keep both). REMAINING: wire grok/wiki/openserp
   to register/should_examine + require Grok-cited URLs + Grok relevance-eval (fetch-then-eval).
5. **[HIGH · data-quality] Centralized JSON result de-duplication (schema-consistent).** Design:
   docs/current/dataquality/RESULT_DEDUP_DESIGN.md. Owner directive: don't repeat JSON results;
   dedup must behave IDENTICALLY across ALL schemas via centralized function(s), not per-entity
   ad-hoc (today ~20 enrichers each `list.append` with no shared dedup; only mentions have a
   local key). Add `src/utils/result_dedup.py::dedup_entity(data, entity)` applied at the ONE
   central write path (`file_lock.write_json_with_lock`/`_validate_entity`,
   `json_validator.validate_and_write_json`), driven by a per-schema DEDUP_FIELDS declaration
   (field -> identity key): URL-bearing result arrays (images/sources/awards/resource_urls/
   primary_sources) dedup on `url_hygiene.clean_url` (SAME normalizer as the URL registry so
   "searched once" and "stored once" agree); mentions on MentionID; free-form on canonical hash.
   First-writer-wins, order-preserved, idempotent. Behavior-only (no schema version bump); lazy
   clean on write-through + optional at-rest sweep. This is the "different function" split out of
   the URL-registry work (not re-SEARCHING a URL != not WRITING a duplicate result).
   reprocessor** — the LAST data-quality layer (prevention + detection are done). Build the
   no-human-gate reprocessor in CORE (resolve-before-write, re-extract the whole source unit,
   deterministic) and run it as the (a) remediation. Highest-risk (first thing to MUTATE the live
   corpus) — design-first + review. (§ second data-quality block, items (a)/(b2).)
3. **[HIGH · deploy] OCR markdown-review UI** — built + tested, not deployed; NAT as an async
   human-gate like dedup. Verify the ComputeStack deploy-path first. (ALSO covers the layout-miss
   task-org tables: that review surface is the same mdreview UI — `table_recovery.py` flags
   `flattened_hints`, `mdreview_ui_handler._scaffold_from_hints` renders them for a human; the old
   "needs design" item is retired, only minor OCR-noise cleanup + optional per-page escape hatches
   remain.)
4. **[HIGH · cost] AWS budget reset + CloudWatch reduction pass** — reset the stale $75 limit
   (S3 alone exceeds it); trim CloudWatch log retention/ingestion. Not a runaway.
   - **[MEDIUM · dedup UX] Human dedup-review UI should SHOW the mention TEXT for context.**
     Entity resolution for people is increasingly an *educated guess* (service number is in
     personal/service records but NOT in book/website mentions, so common-name links rest on the
     weaker name+unit+rank+date compound). The reviewer in `dev-wwii-dedup-ui` currently sees
     entity fields but not the actual sentence(s) each mention came from. Surface the mention
     `original_text`/`verbatim_reference` (via the `mentions`/`event_mentions` junction →
     `output/content/<book>/<chapter>-parsed.json`) in the duplicate-group detail view so a human
     can judge "same Smith?" against real context. Pairs with the person-disambiguation design
     (service# auto-link / unit+rank+date review / bare-name unresolved). Not a priority now.
5. **[HIGH · enrichment] SourceRechecker into People enrichment** (source-first gap-fill of
   `nationality` etc. in `enrich_person_biography`, mirroring people_groups) + Grokipedia should
   fetch the real `/page/<slug>` article instead of caching raw search-results HTML
   (`enrich_biographies.py`). Minor residuals: OpenSERP stamps `openserp_searched` on empty/failed
   search; `time.sleep(1)` on cache hits; HallOfValor name-match recall. (The earlier "verifiers
   fail-OPEN" + "phase3_handler double-enrich" items are DONE — verified: `_verify_result` is now
   fail-CLOSED and the Phase-3 people path is OpenSERP-only / phase3_handler is unwired.)
   - **[HIGH · enrichment] Phase 2 should capture place HISTORICAL NAMES / ALIASES from
     Grokipedia/Wikipedia.** Verified gap (2026-10-14): the Phase-2 place enricher
     (`src/extraction/enrich_places.py::enrich_place_from_wikipedia`) writes only
     `wikipedia_url` + a lead image + `hierarchy` (continent/country, from `_parse_wiki_page`
     categories). It does NOT extract native-language / former / alternate place names, even
     when the article has them; `enrich_place_from_grokipedia` only stores the URL. Today
     `historical_names[]` is populated ONLY in Phase 3 from Grok KNOWLEDGE (`_merge_historical_names`)
     — model-recalled, not source-cited. FIX: have the Phase-2 Wikipedia fetch
     (`_fetch_place_wikipedia_full`, already pulling extract+categories) ALSO pull the infobox /
     former-name / native-name fields (e.g. `prop=pageprops` + a Wikidata `P1448`/`P1705`/native-label
     lookup, or infobox parse), and the Grokipedia article body, and merge them into
     `historical_names[]` tagged `source: wikipedia|grokipedia`. SOURCE-GROUNDED aliases > Grok-recalled
     ones, and native WWII names (e.g. German "Pfalz" for Palatinate) are first-class per the project
     model — they feed Phase-3 OpenSERP name-variant search + geocoding context. Additive; dedup
     against existing historical_names by name.
   - **[HIGH · geocoding] Phase 3 geocode MISS-RECOVERY (translate + web-search hint).** Today the
     geocode cascade is Nominatim → hill → Grok; a miss stamps `geocode_not_found` with NO further
     recovery (`src/enrichment/places_grok_geocode.py` + `nominatim_geocode.py`). The existing
     `_alias_names` retry only reuses names ALREADY on the record. Two recovery tiers to add,
     tried only on a not-found, scoped by the record's country hint:
     (a) **Native-language toponym retry** — anglicized WWII toponyms miss because the gazetteer
       knows the native form ("Berange Farm" → "Berange Ferme"/"Ferme de Béranger"; Château-/Schloss-,
       Bois/Wald, Mont/Berg, etc.). Do cheap deterministic generic-token substitution
       (Farm→Ferme/Hof, Castle→Château/Schloss/Burg, Wood→Bois/Wald, Hill→Mont/Berg, River→Rivière/Fluss)
       + a Grok translate-to-native fallback, then re-query Nominatim.
     (b) **Web-search location-hint rescue (OpenSERP)** — for real-but-obscure toponyms (e.g.
       "Bliesgersviller" → a vacation-rentals page revealing "Bliesgersviller, Lorraine, France").
       CRITICAL: do NOT WWII-relevance-gate here — the page's WWII relevance is irrelevant; we only
       extract an EXISTENCE + LOCATION hint (region/country, or coordinates) from titles/snippets via
       Grok, then re-run Nominatim/Grok geocode with that enriched context. This is the OPPOSITE of
       the normal relevance-gated OpenSERP use.
     (b2) **EXIF-GPS from a discovered PHOTO** — when the web-search rescue (b) surfaces an IMAGE of
       the site (e.g. a photo of the Weisser Weh road), opportunistically read its EXIF GPS IFD
       (PIL `Image.getexif()` → GPSLatitude/GPSLongitude → decimal) as a HIGH-PRECISION coordinate
       candidate, tried BEFORE the text-hint parse. CAVEATS (treat as a hint, guard hard):
       (i) social-media platforms (Instagram/Facebook/X) STRIP EXIF on upload — an Instagram-hosted
       image almost always has NO GPS; don't rely on it, fall through to (b). EXIF survives mainly on
       archive/museum/Flickr originals + some blogs. (ii) GPS is where the photo was TAKEN, not
       necessarily the subject (usually close enough for a place). (iii) SANITY-GUARD: accept only a
       valid-range coordinate that is plausible vs the country hint; else discard. A modern photo of a
       still-existing feature gives the right modern coordinate (fine for geocoding).
     (c) **Persist back:** store the discovered native name as a `historical_name` and the
       region/country into `hierarchy`, tagged with provenance (`geocode_source: web_rescue` /
       `translate` / `photo_exif`), so retries + the Phase-3 name-variant search compound and it's
       cached. Cost is bounded (not-found is small, ~3% hard-miss; one-time per place, idempotent).
       Pairs with the Phase-2 native-names item above (that captures names upstream; this recovers
       when they're still missing at geocode time).
   - **[HIGH · extraction+geocoding] Capture CO-REFERENCED place aliases from surrounding text.**
     Verified gap (2026-10-14): the places extractor (`src/extraction/places.py::PlaceMention`) has
     `current_name` + a SINGLE optional `historical_name` — it cannot keep the full alias set a
     source gives in one phrase. Example: "Bogeyman Hill (Hill 697, the Schwarzer Mann)" names the
     SAME place three ways — a GI nickname (ungeocodable), the military grid designation "Hill 697"
     (geocodable from WWII map refs), and the NATIVE German toponym "Schwarzer Mann" (almost
     certainly in OSM). We keep "Bogeyman Hill" and dead-end at `geocode_not_found`, even though the
     geocodable aliases are RIGHT THERE in the source text. (Bonus: "Bogeyman" is literally a
     translation of "Schwarzer Mann" — the nickname itself hints the native name.) TWO parts:
     (A) **Extraction model is the real array change (NOT the output schema's shape).** The
       OUTPUT schema already HAS `historical_names` (array; items `string|object`) and `aliases`
       (array of strings) — the persisted record can hold the full set today, and richer alias
       OBJECTS already validate (items permit `object`). The gap is the Pydantic EXTRACTION model
       `src/extraction/places.py::PlaceMention`, whose `historical_name: Optional[str]` is SINGULAR
       — that's what limits Grok to one alternate name. FIX: change the extraction model's singular
       `historical_name` → an ALIAS ARRAY (+ prompt) capturing ALL co-referenced forms in patterns
       like "X (Y, the Z)" / "X, also called Y", each tagged by type
       (`nickname` | `hill_designation` | `native` | `former`) + language; populate
       `historical_names[]`/`aliases` so the EXISTING Nominatim alias-retry + Phase-3 name-variant
       search use them. OPTIONAL (recommended) output-schema change: tighten `historical_names`
       items from the permissive `string|object` to a documented object
       `{name, type, language}` with `additionalProperties:false` — THAT change needs a
       `people`-style version bump (`ENTITY_SCHEMA_VERSIONS["places"]`) + fingerprint re-pin in
       `tests/test_schema_fingerprint.py`. Without the tightening, no output-schema bump is required
       — only the extraction model + prompt.
     (B) **Geocode recovery (cheapest tier — no web/API):** on a geocode miss, RE-READ the place's
       own `original_text` (+ neighboring sentences from the parsed source) and extract alternate
       names via Grok, then retry the cascade. The alias is already in OUR source text — this
       should run BEFORE the web-search/translate tiers above. Cross-linked to the geocode
       miss-recovery item (a/b/b2/c) and the Phase-2 native-names item.
6. **[HIGH · security hardening] CloudFormation review follow-ups** (full review 2026-10-11,
   0 crit / 3 high / 6 med — see `CFN_SECURITY_REVIEW_2026-10-11.md`): HIGH = ~~Chatbot channel
   `GuardrailPolicies` (defaults to AdminAccess)~~ ✅ DONE PR #347; remaining HIGH = GitHub-deploy-role
   `iam:PassRole` lacks a `PassedToService` condition, internet-facing dedup/mdreview/metrics Lambdas
   share the broad LambdaExecutionRole. MED = entity-table PITR, deploy-role GetSecretValue,
   TriggerQueue SSE+DLQ, OcrSpotController `batch:*` on `*`, public dedup/metrics APIs,
   constant-time auth compare.
7. **[HIGH · sourcing] Source qualification** — derived qualifier on the bibliography record so a
   reader (and RAG/UI) can judge how a source qualifies for an asserted fact (qualification, NOT
   veracity): `source_type` (primary/secondary/tertiary/reference) + `proximity` + `evidentiary_support`,
   each with method/basis/confidence. Additive bibliography schema bump (no new schema, no reprocess);
   derived from signals already captured (NARA archive ref, document_type, footnote density);
   read-through `event→mentions[].BibliographyID→bibliography`. Dublin Core documented as an option
   (direct DCMI profile vs. derivation/crosswalk). Design agreed 2026-10-12 — see
   `dataquality/SOURCE_QUALIFICATION_DESIGN.md`; pending owner sign-off on DCMI direct-vs-derivation.
8. **[MED] data-quality follow-ups** (validation_stats time-series, fail-open-allow counter,
   persisted corpus_quality_report dashboard, dedup precision/recall, catcher per-book dimension)
   + observability (Slack refinement, misleading batch log, SNS IAM) + bibliography/Archive.org UIs.
   + **incremental referential-integrity audit**: `audit()` now does a SINGLE whole-corpus read
   (was N+1: once for PKs + once per edge) and the per-phase call is timeout-bounded + skippable,
   but it still re-reads the WHOLE corpus each run. End-state: persist the PK index + per-record
   `_source_hash`, and on a run re-read only NEW/CHANGED records, reconciling both reference
   directions (a new record referencing an existing one; a changed/removed record dangling an
   existing reference). Needs durable index + change-detection; design-first (own task).
9. **[LOW] external_maps license config exposure; code-quality refactors; mypy --strict on new
   files; doc-hygiene pass (see docs/archive/2026-10-11/UNVALIDATED_CLAIMS_FOR_REVIEW.md).**
   + **atomic _update_manifest**: `lambda_handlers/trigger_handler.py::_update_manifest`
   is a read-merge-write (get -> set(existing+new) -> put) that LOSES updates under
   concurrent SNS fan-out. Book-scoped Phase 1 now bypasses it (syncs the full book
   prefix), but any non-book-scoped/manifest path is still vulnerable. Make it atomic
   (DynamoDB list_append / conditional write) or drop the manifest for per-book queues.
   + **s3 skip-existing for book-scoped Phase 1**: `_download_phase1_inputs` now
   re-downloads the WHOLE book prefix each run (correctness over cost); add an
   etag/mtime skip to avoid re-fetching unchanged chapters on retries.
10. **[FUTURE] Postgres/pgvector + RAG layer, DynamoDB-removal, true multi-job concurrency, Step
    Functions, new entity types — gated on the post-ingestion RAG phase.** RAG + the eventual UI
    MUST expose `bibliography.source_qualification` (source_type/proximity/evidentiary_support +
    basis/confidence) as a queryable facet/ranking signal, read through
    `event→mentions[].BibliographyID→bibliography` (see `dataquality/SOURCE_QUALIFICATION_DESIGN.md`).

Detail for each item follows in the sections below (grouped by original theme; status markers are
authoritative where they conflict with this index).

### [HIGH] Data-quality program — data-scientist review backlog (2026-10-09)
From an independent data-scientist review of `DATA_QUALITY_SAFEGUARDS.md`. Key reframe: the write
guard defends against **structural (schema-shape) corruption at write time**, but the dominant
risks for this corpus + audiences (historians: citations/provenance; genealogists: person/unit/
place/date lookups) are **corpus-level integrity** and **semantic/content** validity — classes the
per-write guard is blind to. "The guard confirms a record is well-formed; nothing confirms it is
informative or correct" (observed: empty-`{}` biographies and 0.0-coord places pass validation).

**HIGH**
_All HIGH items in this block (1 cross-reference integrity, 2 semantic flags, 4 catcher low-info,
4b Phase-1 guard) are DONE — moved to "Completed data-quality program" at the bottom._

**MED**
5. **Persist validation_stats as a time series** (per run + per book) and alert on TREND/regression,
   not just absolute threshold; make the systematic rate per-(entity × validator-keyword × BOOK)
   with a lower count floor for small entities (People 1,650 / Maps 55 can hide a real cluster
   under the 5%-of-entity rate today).
6. **Fail-open-allow counter** — the in-process guard fails OPEN on validator/registry/import error
   (§1.4); add a counter so silent allows are observable (a systematic import failure would show a
   clean block_rate while admitting corruption).
7. **Persisted corpus_quality_report** (nightly, versioned, dashboard): null-rate per field,
   provenance-coverage %, cross-ref-resolvability % per edge, dedup drift/collision rate,
   geocode-in-theatre %, date-in-window %, enrichment status. Auto-regenerate the stale
   DATA_QUALITY_STATUS numbers from it.
8. **Dedup precision/recall** against a small labelled set (can't tell under- vs over-merging today).
9. **Catcher per-book/per-prompt dimension** to actually distinguish extraction-defect (spread
   across books) vs source-defect (concentrated in one book) — the docstring claims this but the
   code only keys on validator keyword.

**LOW**
10. Cross-source corroboration flags across the 3 overlapping ETO books.
11. Surface a corpus-wide, queryable `confidence` field for historian filtering.
12. Doc fixes: ULID-repair scope warning; concrete audit-invariant definitions (ref-graph edges);
    align the validation_stats docstring to the code.
13. **Expose `external_maps` license/download config** (surfaced by the 2026-10-11 doc audit) —
    `phase2_extract.py` reads `external_maps.{storage_backend, allowed_licenses}` (and the maps
    path reads `download_images`/`image_storage_path`) via `config.get()`, but `config.yaml` only
    ships `external_maps: {enabled: false}`, so the license-filter / download knobs are
    undiscoverable + unsettable. Add them to `config.yaml.example` with defaults + a comment (or
    confirm intentionally code-only). Small, enables historian-facing license control.

### [HIGH] Data-quality follow-ups from the write-validation centralization (2026-10-09)
Added after centralizing the write guard across all paths (PR for `fix/write-validation-always-on`).
Two detective/corrective layers remain (the preventive in-process guard is done + merged):

- **(a) Remediate pre-existing invalid S3 records.** Pre-fix, the unguarded dedup/merge path wrote
  schema-invalid fragments to S3 (sampled: ~28/80 across people/people_groups/places/dates — all
  version-LESS, missing primary-key ID + name, i.e. unresolved per-mention fragments). They carry
  UNIQUE cross-ref mentions (MentionID/EventID/Sub_eventID with zero overlap vs valid records), so
  **do NOT delete** (orphans mentions). Remediation: **quarantine** to `output/_quarantine/` →
  **targeted reprocess** from each fragment's provenance (book + EventIDs) to regenerate canonical
  PK'd records + re-run dedup so name-variants merge → purge quarantine only after mentions land in
  canonical records.
- **(b2) Corrective loop (NO human gate — code, not people).** On a bad finding: auto-classify +
  quarantine (tag), then AUTOMATED, dedup-aware REPROCESS from the record's provenance (book +
  EventIDs), with an idempotency/loop-guard (same failure N times → mark `unrecoverable`, stop
  retrying, keep quarantined). The feedback signal is STATISTICAL, not case-by-case: the
  **validation-stats catcher** (DONE — `src/utils/validation_stats.py`, writes
  `output/metrics/validation_stats.json`, emails/Slacks on systematic clusters) is the upstream-bug
  report. Humans fix CODE in response to a systematic-cluster alert; they never adjudicate
  individual records. Isolated failures auto-reprocess silently; only systematic clusters alert.
  **REPROCESSOR ACCEPTANCE CRITERIA (hard — these distinguish a reprocessor that HEALS from one
  that silently FRAGMENTS the corpus):**
  - **Only Tier-3 content** triggers reprocess (PK ULIDs self-heal in the guard; bad reference IDs
    → referential-integrity audit). "Work up the chain" = re-extract the missing CONTENT field from
    the SOURCE (the record's provenance → `output/content/<book>/<chapter>-parsed.json` + original
    source + summary data). Source is the authority for content.
  - **Re-run the RELEVANT extractors for the whole SOURCE UNIT, not one schema.** A single passage
    yields interlinked people/places/units/dates/events; fixing one entity in isolation leaves its
    cross-refs inconsistent. Re-extract the unit, not the one broken record.
  - **MUST resolve-before-write — NEVER duplicate an existing entity.** A source passage references
    entities (e.g. "McAuliffe") that ALREADY EXIST as canonical records. Every re-extracted entity
    goes through dedup/entity-resolution against the existing corpus: resolve → **MERGE into the
    canonical record** (preserve its existing PK, union mentions) or heal-in-place; create ONLY if
    genuinely new. The existing corpus is the authority for IDENTITY. Re-extraction that writes a
    fresh record for an already-known entity is a BUG (it fragments the corpus — the exact pathology
    we are fixing). Resolution must be DETERMINISTIC so repeated runs make identical decisions
    (idempotent, loop-guard-safe).
0. **~~[CRITICAL] Deploy current `main`~~ ✅ DONE 2026-10-02** — verified live: S3
   AES256 + DenyInsecureTransport; pandoc `--sandbox` image pushed; AV scanning
   deployed + signatures seeded + EICAR-validated (`AV_SCAN_ENABLED=true`); EBS
   encryption (account default + launch-template). No Critical items remain open.
1. **~~[HIGH] Fix idle-infra teardown + add an hourly backstop reaper~~ ✅ DONE 2026-10-11
   (code-complete, not yet deployed).** (a) ROOT CAUSE fixed in `lambda_handlers/openserp_manager.py`:
   the main active-work check now counts only PIPELINE tasks (`[t for t in taskArns if "openserp"
   not in t]`), so an idle OpenSERP service no longer counts its own task as work and can be torn
   down. (b) BACKSTOP: new `lambda_handlers/idle_backstop_reaper.py` on an HOURLY EventBridge rule —
   progress = S3 `output/` object count; stores a strike counter in DynamoDB (`reaper#state`);
   force-scales OpenSERP→0 + invokes nat-manager delete + alerts the phase2-complete SNS topic ONLY
   after TWO consecutive no-progress hours with NAT up and no lock/lease; resets on any
   progress/lock/NAT-down; fail-safe (never reaps on error). CFN (`IdleBackstopReaperFunction` +
   hourly schedule + permission, compute.yaml) + `update_lambdas.sh` loop + 6 unit tests. Full
   gate PASS (1993). Deploy via `deploy_all.sh` + `update_lambdas.sh` when next deploying.
   (Original IMMEDIATE "manually scale dev-wwii-openserp to 0" was a one-time manual action.)
2. **~~[HIGH] Re-OCR the off-by-one corpus~~ ✅ DONE (verified vs S3 2026-10-11)** — St. Vith/Boyer +
   ETO OOB markdown re-OCR'd (post-fix page-range chunks on S3, no shifted dirs). Was "wrong data
   feeding extraction"; now corrected.
2. **[HIGH] Deploy + wire OCR markdown-review UI** — now **UNBLOCKED** (stack
   healthy; deploy path proven this session). Deploy + smoke-test the `/mdreview`
   route; treat NAT as an async human-gate like dedup.
3. **[HIGH] Verify page-separator vs page count after merge** — data-integrity
   guard before markdown feeds parsers; pairs with the re-OCR work.
4. **[HIGH] Cost: budget 6× over** — reset the (stale) $75 limit + CloudWatch $85
   reduction pass. Largely explainable (one-time $135 domain), not a runaway.
5. **[HIGH] Layout-miss task-org tables** — review surface BUILT (= the mdreview UI; see the
   Layout-miss detail section). "Needs design" retired; residual = OCR-noise cleanup + optional
   per-page escape hatches.
6. **[MED] Slack refinement; misleading batch log; SNS:ListSubscriptions IAM;
   bibliography + Archive.org enrichment UIs; delete stale chunk dirs.**
7. **[LOW/REGRESSION] mypy --strict on new files; the 5 regression tests;
   code-quality refactors.**

### Backlog items added 2026-10-08 (AWS Phase-3 E2E findings)
- **[DONE 2026-10-08] OpenSERP bumped v0.6.0-15 -> v0.8.12 + no-sandbox -> search WORKS.** The
  version bump RESOLVED the anti-bot "Found 0 results": v0.8.12's improved scraping now returns
  real Google results (Found 111/203/1.6M...), and end-to-end OpenSERP enrichment is validated in
  AWS — Grok fail-closed verify correctly accepted "Maj Gen Allen W. Jones, 106th Infantry" +
  rejected Clara Barton/obituary false-positives; "OpenSERP enriched: Alan W. Jones". The bump
  also cleared the stale Go crypto/tls CVEs. Submodule re-pinned to v0.8.12 + a local no-sandbox
  patch; task-def uses `serve --host --port` (no --raw). REMAINING: (a) OpenSERP BASE image
  (chromedp/headless-shell@sha256 pin) has ~51 fixable OS-pkg HIGH/CRITICAL (util-linux etc.) ->
  refresh the base-image digest so Trivy gating passes on deploy; (b) occasional per-engine
  blocks (Yandex) + per-search browser latency can still approach the pipeline's 30s request
  timeout -> consider raising it / proxy. 
- **[SUPERSEDED by the v0.8.12 bump above] OpenSERP Chromium --no-sandbox fix.** Root-caused the empty
  results: (1) task ran `--raw` (raw HTTP engine, blocked by search engines, no image search) —
  removed, registered dev-wwii-openserp:5 (browser mode); (2) browser mode then FATAL-crashed
  (Chromium zygote sandbox) in Fargate — FIXED by adding .Set("no-sandbox") to the rod launcher
  in the openserp submodule (built, Trivy-flagged fixable Go CVEs [stale deps], pushed, redeployed,
  verified: browser now launches + navigates google/bing/yandex, 0 FATAL). REMAINING (needs the
  v0.8.12 version bump + proxy/captcha): engines return "Found 0 results" (anti-bot/consent-page
  blocking the datacenter IP) + the per-search browser time can exceed the pipeline's 30s request
  timeout -> breaker still opens. The submodule now carries a local patch -> fold into the version
  bump. Also: Trivy gating would BLOCK the current openserp image (fixable Go crypto/tls CVEs) ->
  version bump also resolves that.

- **[HIGH] OpenSERP submodule is stale (v0.6.0-15 pinned vs upstream v0.8.12 / 78 commits
  behind) + build script does not verify component versions.** OpenSERP scrapes live search
  engines, so staleness likely contributes to empty results (markup/anti-bot drift). (a) Update
  the `openserp` submodule to the latest release tag (v0.8.12) — CAREFUL: CLI/config changed
  upstream (e.g. --log_level rename), so re-verify the task-def command (`serve --host --port`,
  NO `--raw` — see the browser-mode fix) still matches; rebuild + Trivy-scan + push. (b) ~~DONE 2026-10-08~~: scripts/check_component_versions.sh + deploy_all.sh preflight now CHECK each vendored/submodule component (openserp,
  chandra, paddle, clamav) against its upstream latest release and warn/fail if behind, so
  components don't silently rot.


- **[HIGH] OpenSERP returns empty results in AWS -> circuit breaker opens, 0 enriched.** A scoped
  Phase-3 ECS run (BOOK_NAME=TheArdennesBattleOfTheBulge, --max-items 2) connected to live
  OpenSERP (10.0.21.65:7001) and issued real queries (incl. the new multi-language place queries
  "Welscheid"/"Winterspelt"/... confirmed working), but OpenSERP's /mega/search returned
  empty/failed 5x consecutively -> breaker OPEN -> all searches skipped, 0 enriched. OpenSERP
  connectivity is fine; its SEARCH BACKEND (real Google/Bing/DuckDuckGo scraping) is returning
  nothing — likely rate-limited/blocked/misconfigured in the container, or needs search-engine
  egress config. Diagnose OpenSERP container health + a direct /mega/search probe. (Geocoding +
  the rest of Phase 3 worked: 2 attempted/2 geocoded, no BLOCKED writes.)
- **[MED] Ad-hoc Phase-3 ECS task without BOOK_NAME does a FULL S3 entity-tree download**
  (unbounded, ~13min+ stall before enrichment). Set BOOK_NAME to scope (confirmed: 700 files,
  fast). Document that ad-hoc Phase-3 runs must set BOOK_NAME or use a manifest.

### Backlog item added 2026-10-07
- **[MED] Write guard vs. BREAKING schema migrations (schema is never fully stable).** The
  central write guard (file_lock.write_json_with_lock) validates a record's SHAPE against the
  entity's *current* enforced schema and is version-agnostic. This is correct for writes +
  additive changes (old records still validate). But during a BREAKING migration, an existing
  record written under an older version that is then re-written (merge/update) could be BLOCKED
  by the guard even though the read-side `schema_contract.register_upgrade` path would upgrade
  it. The guard does not consult the upgrade registry. Mitigation today: additive-only is the
  norm (the `WWII_WRITE_VALIDATION=off` escape hatch was REMOVED — validation is now
  unconditional, so a breaking migration cannot be worked around by disabling the guard).
  Future: have the guard defer to register_upgrade (or validate against the record's declared
  `_schema_version` schema when it is older-but-registered) so a breaking migration doesn't
  block in-flight re-writes. Low
  frequency (breaking changes are rare + deliberate) but real.
- **~~[HIGH] Install Chromium in the Phase-2 container image~~ ✅ DONE 2026-10-08** — added
  `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` + `RUN python3 -m playwright install --with-deps
  chromium` to the main `Dockerfile` (runtime stage, shared world-readable path). Validated: the
  image builds and the non-root `pipeline` user launches headless Chromium + renders inside the
  container. **Remaining op:** rebuild + push the image on next deploy and confirm it passes the
  Trivy AV/vuln scan (the browser + its OS libs enlarge the image + attack surface).
- **[MED] Promote `source_section` article endnote references → `bibliography`,
  de-duplicated by URL.** The `source_section` entity already captures Wikipedia
  article references inline (`reference_articles[].references[]`, tagged
  `source: "wikipedia-reference"`; Grokipedia refs not yet extracted). Goal: collect
  the footnoted references for later evaluation as a durable, de-duplicated set.
  Design decided 2026-10-07:
  - **Where:** promote each unique reference into a first-class `bibliography` record
    (resolvable via the existing NARA/Archive.org resolver), NOT left duplicated inline.
  - **Dedup key = the reference URL (normalized).** Two sections citing the same
    reference → ONE bibliography record + multiple back-links.
  - **Pointer approach:** `source_section` keeps lightweight reference-ID pointers to
    the deduped bibliography records (not full copies), so there is a single copy of
    each citation and the section still records which it cited.
  - Keep secondary (Wikipedia-cited) provenance clearly distinguished from
    primary-source citations.
  - **Why it matters:** a single operation (e.g. Battle of the Bulge) spans ~15 book
    chapters → 15 `source_section` records each fetch the same article → ~2,000
    duplicated reference entries today. URL-keyed bibliography records collapse that.
  - **Related follow-on (separate):** the same cross-section redundancy affects article
    TEXT and MEDIA (same Bulge article/images stored 15×). Root cause is that
    `operation` is an inline label repeated per section. Consider a canonical
    `operation`/campaign entity (keyed by normalized name/`wikipedia_title`) that holds
    the article + references + media ONCE, with sections referencing `OperationID`.
    Not scoped yet — flagged for when operation dedup becomes worth it (measure the
    actual section→operation fan-out first).
8. **[FUTURE] Postgres adapter, DynamoDB-removal, multi-job concurrency, Step
   Functions, new entity types — gated on the post-ingestion RAG/Aurora phase.**

Not-code (deferred, no action): SHAEF OB map gaps (NARA acquisition).

---

## Critical (actively losing data or breaking pipeline)

_None open._

#### ~~Deploy current `main` to AWS~~ ✅ DONE + verified live 2026-10-02
Deployed this session (verified against live AWS): S3 `AES256` +
`DenyInsecureTransport`; pandoc `--sandbox` image pushed (pulled on next convert
task); AV scanning deployed (`AvScanTaskDef`/freshclam/schedule, signatures
seeded, EICAR→infected validated, trigger `AV_SCAN_ENABLED=true`); EBS encryption
(account default `true` + launch-template `Encrypted: true`). deploy_aws.py +
deploy_all.sh now manage video/clamav images (PRs #199/#202). Remaining deploy
sliver is the OCR-review UI — tracked as its own High item (unblocked now).

## Recently Completed (2026-09-23) — OCR reliability + table recovery

_All deployed and validated end-to-end on the St. Vith / Boyer book (252-page
scanned PDF)._

#### ~~Chandra OCR killed by 1h wall-clock timeout on healthy dense chunks~~ ✅ Fixed + deployed
Progress-watchdog (`scripts/ocr_watchdog.py`) is now the primary failure
detector — fails on no-page-progress for `OCR_NO_PROGRESS_SECS` (900s), not
elapsed time. `AttemptDurationSeconds` raised 3600→14400 as a loose backstop.
Wired via `chandra_entrypoint.sh` + `Dockerfile.chandra`; 7 tests. Chandra image
rebuilt+pushed, CFN job def at rev 13 with the new env/timeout.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~submit_ocr_job.py chunk-000 collision overwrites prior OCR output~~ ✅ Fixed
Page-range/manifest re-runs all wrote to `chunk-000/` (submission-local index),
silently clobbering earlier chunks. Output dir now derived from the page range
(`chunk-p0001-0050`, zero-padded/sortable) in all submit paths + publish.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Auto-route flattened 2-D tables to PP-StructureV3 after OCR~~ ✅ Fixed + validated
`src/ingestion/chunk_pages.py` maps `--paginate_output` chunk markdown to
physical PDF pages and flags pages with a flattened task-org table;
`submit_ocr_job.py:auto_route_table_recovery()` fires after OCR (best-effort,
`--no-recover-tables` opt-out), submitting one Paddle job per flattened page.
Validated: OCR → detect pp.154/155/156 → recovery submitted with no manual
steps; p155 produced a reconstructed `<table>`.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~PP-StructureV3 recovery recovered 0 tables (libgomp1 missing)~~ ✅ Fixed + deployed
Paddle worker crashed at import (`libgomp.so.1: cannot open shared object
file`) → CPU fallback → also crashed. `Dockerfile.paddle` now installs
`libgomp1`. Image rebuilt+pushed; re-run confirmed `device=gpu` and a recovered
table.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~NAT torn down while async recovery jobs still pending~~ ✅ Fixed
`auto_route_table_recovery` submits Paddle jobs asynchronously; `--wait` used to
tear NAT down as soon as the Chandra job finished, stranding recovery jobs
(which need NAT for first-run model download). `_wait_and_publish` now waits for
the recovery jobs to drain before releasing networking (Option B —
"networking stays up while work pending").
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Chandra image full rebuild fails on torch CUDA-dep hash drift~~ ✅ Fixed
`docker build --no-cache -f Dockerfile.chandra` failed at the `torch==2.5.1+cu121`
install: pip resolved torch 2.5.1's declared CUDA deps (`nvidia-cudnn-cu12`,
`nvidia-cusparse-cu12`, …) whose upstream hashes on the pytorch CDN no longer
matched torch's recorded metadata (a different nvidia-* pkg failed each run).
Fix: install torch/torchvision `--no-deps` (skip that resolution), then install
the exact CUDA runtime deps torch needs (incl. `nvidia-cudnn-cu12==9.1.0.70`)
from PyPI, which has no stale hash-file enforcement; a build-time `import torch`
asserts the result loads. Verified in isolation. The `Dockerfile.chandra.overlay`
is no longer needed for a full rebuild (kept as a fast code-only-change helper).
*Source: St. Vith end-to-end test 2026-09-23*

---

## High Priority (produces wrong results or wastes significant resources)

#### Wire the reusable SourceRechecker into PEOPLE enrichment
`src/extraction/source_recheck.py::SourceRechecker` (reusable, entity-agnostic) is built
and already used by people_groups (`group_source_recheck.py`) to recover missing critical
fields (nationality, CC parent division) from retained `event_mentions[].original_text`
BEFORE external enrichment — source-first, gap-fill-only, provenance-stamped, fail-safe.
**Apply the same pattern to People**: when a person is missing a critical field
(esp. `nationality` — needed for award-sourcing gate + Wikipedia disambiguation), recheck
the retained source text first. Define a `FieldRecheckSpec` for people and call it in
`enrich_person_biography` before the Wikipedia/award steps. (Owner-requested 2026-10-05.)
The same should extend to other `docs/current/features` entity types (places, equipment)
with missing required data.

#### Enrichment-review findings carried forward (from archived PHASE3_REVIEW + SUPPLEMENTARY_SEARCH_REVIEW, 2026-10-02)
These were captured in the two 2026-10-02 read-only review docs (now in
`docs/archive/`). The top items (geocoding C1 PR#217, failure-visibility C2/C3 PR#221,
M3/H3 PR#222, award sourcing) were fixed; the following remain **open in code** and are
tracked here by name so they are not lost:
- **Grokipedia stores raw search-results HTML as the "biographical source"**
  (`enrich_biographies.py:~175` `cache_result("grokipedia", name, response.text)`) —
  low-signal input; fetch + cache the actual `/page/<slug>` content instead. (SUPP #1/#4)
- **~~Verifiers fail OPEN~~ ✅ DONE (verified 2026-10-11)** — `_verify_result`
  (`openserp_enrichment.py`) is now explicitly FAIL-CLOSED (no grok_client or any Grok error →
  `return False`, reject the unverified result).
- **~~phase3_handler double-enrich path~~ ✅ DONE (verified 2026-10-11)** — the Phase 2/3 split is
  now explicit: Phase 2 owns ALL Grokipedia/Wikipedia bio enrichment; the Phase-3 people path is
  OpenSERP-only (`phase3_handler.py` comment "We do NOT call enrich_person_biography here";
  `phase3_enrich_data.py` `openserp_people` step), and `phase3_handler` is not wired to any
  CFN/trigger. No double-enrich divergence.
- **OpenSERP stamps `openserp_searched` even on an empty/failed search** — re-runs then
  skip legitimately-unsearched people. (PHASE3 H2)
- **Unconditional `time.sleep(1)` even on cache hits** in the OpenSERP path. (PHASE3 H4)
- HallOfValor **name matching / search recall** (last+initial over-matches AND surname
  particles / Jr-Sr suffixes under-match) — dedicated hardening pass with a real-person
  precision/recall test set. (person-capture review C1/H1/H2)
- Theater/nationality/Western-name assumptions pervasive in supplementary search prompts.


#### Cost: AWS spend ~6× the $75 budget — reset limit + CloudWatch reduction pass
Found 2026-10-01: `dev-wwii-pipeline-monthly` budget **ACTUAL $471 vs LIMIT $75**
(MTD). Decomposed via Cost Explorer by service (Sept) — mostly explainable, NOT a
runaway, and NOT Grok (xAI bills separately):
- **Amazon Registrar $135** — ONE-TIME domain registration (not recurring).
- **CloudWatch $85** — genuinely high for this workload; the real inefficiency to
  chase (log retention/ingestion + ECS container-insights). **Actionable.**
- **S3 $78** — the ~500 GB corpus; expected + growing (already > the whole $75).
- **VPC $54 / EC2-compute $44 / EC2-other $29 / ECR $16** — NAT during runs + GPU
  OCR + Fargate + image storage; consumption-driven, no idle leak (verified: NAT 0,
  GPU 0, no ALB at rest).
Two actions: (1) **reset the budget limit** to reflect the real corpus+GPU
footprint (the $75 predates the 500 GB corpus — S3 alone exceeds it); (2) a
**CloudWatch cost-reduction pass** (log-group retention policies, trim ingestion,
reconsider container-insights). High (resource waste), not Critical (no runaway;
no correctness impact; biggest line is one-time). Use AWS Cost Explorer/Budgets
for any forward projection.
*Source: critical survey 2026-10-01 (budget alarm $471 vs $75)*

#### ~~Re-OCR the off-by-one corpus~~ ✅ DONE (verified against S3 2026-10-11)
The OCR page-range off-by-one is **fixed in code** (`_to_chandra_range`), but all
OCR output produced before the fix is shifted by one page and must be **re-OCR'd**
to be correct: notably `stvith_boyer_full` (St. Vith/Boyer) and any ETO OOB
Chandra markdown generated via the old path. This is wrong *data* feeding the
parsers/extraction, so it ranks above cleanup/observability. No image rebuild
needed — it's a client-side `submit_ocr_job` re-run (now auto-triggered on
re-upload). Scope + cost per corpus TBD. Pairs with the page-separator check
(below) and the stale-chunk-dir cleanup (Medium) after re-OCR is verified.
*Source: M1019 off-by-one 2026-09-24; promoted to High 2026-10-01*
**STATUS — ✅ DONE (verified 2026-10-11 against S3).** Code fix (`_to_chandra_range`) merged AND the
data was re-OCR'd: `s3://dev-wwii-data-pipeline/ocr-output/stvith_boyer_full/` and
`.../ETO_Order_of_Battle/` both contain ONLY post-fix page-range chunk dirs
(`chunk-p0001-0050 … chunk-p0251-0252` / `… chunk-p0601-0602`) with NO old shifted `chunk-NNN`
dirs. The off-by-one corpus is corrected. (Stale local `tmp/stvith_backup/chunk-00N` is a backup,
not live data.) Remaining tie-in: the page-separator-vs-page-count check below can run against the
re-OCR'd output; the "delete stale legacy chunk dirs" MED item no longer applies to these two.

#### Verify page-separator count vs. page count after OCR merge — PROMOTED from Medium 2026-10-01
`stvith_boyer_full` merged with **246 separators for 252 pages** (6 short).
Likely blank/near-blank scans (Chandra emits no separator), but a missing
separator **shifts per-page mapping** — a data-integrity risk before the markdown
feeds the OOB parsers / extraction. Add a lightweight post-merge check reporting
`separators vs. pages` + listing pages with no separator for a quick blank-page
eyeball. Belongs with the re-OCR work (verify the re-OCR'd output here).
*Source: stvith_boyer_full re-OCR 2026-09-26; promoted to High 2026-10-01*

#### ~~OCR page-range off-by-one dropped the first page of every range~~ ✅ Fixed (code); re-OCR needed
`submit_ocr_job.py` emitted **1-based** `--page-range` values, but Chandra's
`--page-range` is **0-based**, matched against 0-based PDF page indices
(`chandra.input.parse_range_str("1-2")` → `[1,2]`). So every OCR job **dropped
the first page of its range and read one page past the end**. Auto-chunk runs
(e.g. St. Vith `1-50`, `51-100`, …) were each shifted by one page — physical
page 1 of every chunk missing, chunk boundaries off by one; a single-page/small
range dropped its first page entirely (found via the M1019 2-page test: only
page 2 OCR'd). Fix: `_to_chandra_range` converts physical N → index N-1 at the
Chandra arg only (chunk-dir naming, logs, manifests, merge mapping stay 1-based
physical). Verified against Chandra's own parser; 6 tests.
**Re-OCR consequence (open):** all OCR output produced before this fix is
off-by-one and should be **re-OCR'd** to be correct — notably the St. Vith /
Boyer corpus and any ETO OOB Chandra markdown generated via this path. Scope +
cost TBD; the fix is deployed-pending (ships in the Chandra path with the next
`submit_ocr_job` run — no image rebuild needed, it's a client-side arg change).
*Source: M1019 language-path investigation 2026-09-24*

#### ~~NAT torn down between compute phases (persistent race)~~ ✅ Done + verified (job-aware NAT leases)
Fixed by the **job-aware NAT lease** system (`src/utils/nat_lease.py`,
CONCURRENCY_AND_NAT_SPEC §4), which replaces the timing-heuristic teardown
(task-count + lock-scan + age windows that raced a PROVISIONING task) with an
explicit cluster-wide **demand** signal. Verified in code + tests 2026-10-01:
- Each network-needing task `acquire_lease` → `heartbeat_lease` → `release_lease`
  (wired in `ecs_entrypoint.py` 2366/2380/2394). A lease exists the moment Phase 2
  starts, so Phase 1's teardown sees demand **before** Phase 2 holds its lock —
  closing the exact "lock not held yet" gap this item described.
- **Every** teardown path consults demand: `nat_manager._nat_demand_present`
  guards both the SNS-completion path and `_delete_all` (direct deletes too),
  cross-checking live leases + running pipeline tasks + in-flight OCR Batch jobs +
  pending queues (ground truth). `ecs_entrypoint._nat_demand_present` likewise via
  `has_nat_demand`.
- Fail-safe: every uncertainty path returns "demand present" (keep NAT up); TTL
  expiry prevents a crashed task leaking demand forever.
- Tests: `test_nat_lease.py`, `test_nat_lease_entrypoint.py`,
  `test_nat_manager_demand.py` (asserts the SNS path keeps NAT when demand),
  `test_nat_readiness.py` — 39 passing.
The intended lifecycle (NAT up across compute, down at the dedup human gate,
final teardown at completion) is realized: a human gate releases its lease →
demand drops → NAT tears down; resume re-creates it. Related open piece: wiring
the **OCR markdown-review UI** as an async gate reuses this same lease/demand
mechanism.
*Source: St. Vith run 2026-09-27; fixed via nat_lease (M3) + verified 2026-10-01*

#### Deploy + wire the OCR markdown-review UI
The markdown-review Lambda + UI is **built and tested but not deployed**
(commit d1c9869): `lambda_handlers/mdreview_ui_handler.py` (two-pane page image
+ editable snippet, saves to `ocr-output/{book}/reviewed/pN.md`), merge-time
substitution + consume-once in `submit_ocr_job.merge_outputs`, and CFN
(`MdReviewUIFunction` + `/mdreview` routes on the existing DedupApi). Remaining:
1. **Deploy** — rebuild the Lambda code bundle + main-stack CFN update
   (`deploy_all.sh` main path), then smoke-test the `/mdreview` route (basic
   auth via the shared dedup authorizer).
   **Deploy-path health (investigated 2026-09-27 — must clear before deploying):**
   the parent stack `wwii-pipeline-dev` is in **UPDATE_ROLLBACK_COMPLETE** (last
   attempt 2026-07-01: ComputeStack "Validation failed with 8 errors"). History
   of recurring ComputeStack update failures. Known causes seen in events:
   (a) `PipelineDashboard` name collision with EventsStack — **appears fixed**
   (compute.yaml now uses `-wwii-pipeline-logs` vs events' `-wwii-pipeline`),
   verify no other duplicate logical/physical names; (b) TaskDef
   "Container.image should not be null or empty" — deploy MUST pass
   `--pipeline-image`/`--openserp-image` (use the `deploy_all.sh` path, not a
   bare `deploy_aws.py`); (c) enumerate the July "8 validation errors" via
   `describe-stack-events` on ComputeStack before retrying. Do a **change-set /
   dry-run first**, and NOT during a live Phase 2/3 run (ComputeStack holds the
   phase task defs + the API).
2. **Direct UI↔pipeline wiring (deferred by design)** — today the UI and merge
   are decoupled via the `reviewed/` prefix. Decide whether a reviewer save
   should trigger anything (e.g. re-merge/re-publish) or stay pull-based at the
   next OCR/merge run.
3. Confirm reviewers can discover the URL + credentials (same pattern as dedup).
4. **NAT management — treat as an async human-review gate (like dedup).** The
   OCR markdown review is an **indefinite human pause**, exactly like the dedup
   gate. So NAT must follow the same lifecycle: **tear down NAT while waiting on
   the reviewer** (no compute is running; don't pay for idle NAT), and **bring
   it back up when review completes** and downstream work (re-merge/re-publish,
   or continued OCR) resumes. Mirror the dedup gate's teardown/resume mechanism
   (the delayed-teardown at the gate + recreate-on-resume) rather than holding
   NAT up. This ties into the "NAT torn down between compute phases" fix: the
   rule is NAT UP across compute, DOWN at async human gates (dedup **and** OCR
   markdown review), final teardown at completion.
*Source: human-review compromise for layout-miss task-org tables, 2026-09-24;
NAT-as-async-gate requirement added 2026-09-27*

#### Layout-miss task-org tables (e.g. p156) → human review (DECIDED 2026-09-24)
**Decision:** sparse/borderless task-org pages that PP-StructureV3's layout
detector classifies as `text` (no `table`-class box — verified on p156 via
`layout_boxes`: text@0.49/0.43 + paragraph_title@0.32, zero table boxes) are
**resolved by human review, not further recovery-engine work**. Ruled out:
`layout_threshold` (nothing to admit) and orientation tuning. This matches the
pipeline's existing "flag, don't fabricate" compromise. The page's structure is
preserved in the recovery JSON `flattened_hints` (group→units, `needs_review`);
`flattened_hints` carry content+grouping, NOT a reconstructed 2-D grid — a
reviewer supplies the column crosstab. See CHANDRA_OCR_DESIGN.md "Recovery
limits & the human-review compromise". Dense tables (p155, OOB corpus generally)
still auto-recover to `<table>`.

**Follow-up — ✅ REVIEW SURFACE BUILT (verified in code 2026-10-11); only residuals remain.**
The "wire flagged pages into a review surface" gap the earlier note called "not yet designed" IS
implemented: `src/ingestion/table_recovery.py` produces the `flattened_hints`/`needs_review` trees
(and deliberately "never reconstructs the grid"), and `lambda_handlers/mdreview_ui_handler.py`
consumes them — `_page_snippet` loads `ocr-output/<book>/recovery/pN.recovery.json`,
`_scaffold_from_hints` renders the groups→units into a starting-point markdown scaffold (with a
"rebuild the 2-D table from the page image, columns not recovered" reviewer instruction), and the
reviewer's edit saves to `reviewed/pN.md`. The CFN function `MdReviewUIFunction` exists in
`compute.yaml`. **So this is NOT separate work — it IS the OCR markdown-review UI (priority #3);
completing/deploying+smoke-testing that one route covers this item.** Genuine residuals only:
(1) residual recovered-cell OCR noise (`1703dd -arch South`) — small cleanup; (2) OPTIONAL per-page
escape hatches, unimplemented and only for a specific high-value page — (A) image-preprocess to make
the grid table-like; (B) crop the columnar region + run the table-structure model directly
(bypass layout detection). The "needs design" label is retired.
*Source: St. Vith end-to-end test 2026-09-23/24; surface verified built 2026-10-11*

#### ~~ULID fix generates different replacements for same invalid ID~~ ✅ Already fixed + now regression-tested
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
**Resolution (2026-09-27):** verified already fixed — `src/utils/json_validator.py`
`_fix_invalid_ulids` builds a `replacement_map` keyed by the invalid value and
`_fix_ulids_recursive` reuses it, so repeated occurrences collapse to one ULID
(the dedup path `src/dedup/merge.py:_replace_id_in_obj` is likewise deterministic).
Behavioral check confirmed same-invalid→one replacement across nested
fields/lists; different-invalid→own id; input not mutated. Added regression test
`tests/test_ulid_replacement_consistency.py` (2 tests) to lock it in.
*Source: CODE_INTEGRITY_REVIEW.md #5*

#### SHAEF OB map corpus gaps → source acquisition (likely a NARA visit)
The `SHAEF OB Maps` daily situation-map set has coverage gaps that limit
date-based queries (e.g. "unit X on date Y → show the map"). These are
**acquisition** issues, not code — deferred; likely require obtaining the
missing sheets from NARA.
- **December 1944 entirely missing** — no `44-12` folder. The Ardennes/Bulge
  month is absent, so any query in Dec 1944 (e.g. "3rd Armored on 15 Dec 1944")
  is unanswerable from this set. Coverage is Oct–Nov 1944 + Jan–Apr 1945.
- **Missing 15 Feb 1945** — the `45-02` folder has 29 files (a single-day gap).
- **`311144 SHAEF OB.jpg` mislabeled** — filename encodes 31 November (does not
  exist); needs disambiguation (likely 30 Nov or 1 Dec) when the corpus is
  revisited.
- Interim behavior: the ingestion should return "no map for that date" rather
  than a wrong neighboring map. See
  `docs/current/dataquality/MAP_IMAGE_AV_INGESTION.md`.
*Source: B405 / SHAEF map assessment 2026-09-26*

#### ~~Wire Phase 0 as an auto-triggered phase + type-routing (close the ingest→extract seam)~~ ✅ Done + verified
Adding a **raw** document now runs source-to-finish unattended; all three manual
seams are closed (verified in code 2026-10-01):
1. **OCR auto-triggers on upload** — `trigger_handler._submit_ocr` (from the
   `_split_by_media` → `ocr_keys` loop) submits the Chandra OCR Batch job
   automatically on a raw PDF/image upload (per-book claim dedup, page-range
   chunking, media-mismatch reject). No hand-run `submit_ocr_job.py`.
2. **Phase 0 → Phase 1 handoff is chained** — `ocr_merge_handler` (EventBridge on
   Batch SUCCEEDED) merges OCR output and promotes it to
   `contentrepository/{book}/chapter1/chapter1-content.md`, whose ObjectCreated
   event triggers parse. CONVERT (`phase0_convert.py`) and VIDEO
   (`phase0_video.py`) write the same chapter contract directly. All four tracks
   converge → Phase 1 → 2 → dedup gate → 3.
3. **Document-type routing at ingest** — `_split_by_media` routes by extension
   (OCR / CONVERT / VIDEO / PARSE); `_is_structured_reference` tags OOB docs with
   a `.structured` marker and the merge handler's `_is_structured`/`_route_to_oob`
   sends structured-reference OCR to the deterministic `phase0_ingest` parser
   (`Phase0IngestTaskDef`), NOT narrative extraction (`test_oob_routing.py`
   asserts it does not write the narrative chapter). An AV gate + reject-net +
   translation now also sit on this path.
The dedup review gate + OCR markdown-review UI remain **intentional** human gates.
Still open separately: the maps/images/film **vision branch** (design only, see
`MAP_IMAGE_AV_INGESTION.md`) and the "reprocess already-imported data after Phase
0 routing lands" backfill (Future/Research).
*Source: wiring review 2026-09-26; closed/verified via the phase0-autotrigger-routing branch (PRs #186-#188) 2026-10-01*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Slack notifications — formatter built, needs refinement
**Mechanism DONE (verified 2026-10-01):** `lambda_handlers/slack_formatter.py`
subscribes to the phase2-complete topic and wraps free-form text in Chatbot's
custom-notification schema (emoji triage, phase-aware "View logs" deep link,
financial amount/Cost-Explorer links), republishing to the Slack topic
(`cloudformation/events.yaml` `SlackFormatterSubscription`). This closed the
original "free-form text silently dropped by Chatbot" gap.
**Refinement (per owner 2026-10-01) — DONE except live-render check:**
- ✅ **Detail parity (PR #347):** Slack now carries the SAME full body email does
  (both subscribe to `phase2-complete`; the formatter preserves the whole body in
  `description`, structures JSON bodies, surfaces a distinct Subject as a bold lead).
- ✅ **Success-vs-failure-vs-review distinction (PR pending):** the emoji triage now
  checks review/gate BEFORE failure, so a dedup gate "HOLD"/needs-review renders as
  an action item (:warning:) instead of a failure (:x:) — the owner-flagged
  false-alarm. Genuine failures (fail/error/denied/not-authorized/exception) still :x:.
- **OPEN:** live-render verification against a deployed stack (confirm the card renders
  as intended in the actual Slack channel) + any further noise-reduction the owner wants.
*Source: St. Vith 2026-09-27; mechanism verified + refinement flagged 2026-10-01; detail+triage done 2026-10-12*

#### ✅ DONE — ECS task role missing `SNS:ListSubscriptionsByTopic` (notification preflight)
`_preflight_notification_subscriptions` (non-blocking) was hitting `AuthorizationError`
because `dev-wwii-ecs-task-role` lacked `SNS:ListSubscriptionsByTopic`. Added the read
permission to the task-role SNSPublish statement (`cloudformation/iam.yaml`) so the
preflight can actually verify subscriptions instead of always warning.
*Source: St. Vith verification run 2026-09-27 log; fixed 2026-10-12*

#### Misleading "0 processed, N failed" log in batch mode
In batch mode, the synchronous core-extraction step does **not** process inline —
it *collects* requests for the xAI Batch API. But it still logs
`Core extraction complete: 0 processed, 140 failed` / `Phase 2 complete: 0
processed, 140 failed` (70 events + 70 dates requests deferred to the batch),
which reads like a total failure when the batch actually submitted fine
(`num_requests: 70, num_success: 70`). Reword the batch-mode path to report
"N requests collected for batch" instead of "0 processed / N failed" so operators
(and the zero-count warning) don't false-alarm. Observability, not correctness.
*Source: St. Vith verification run 2026-09-27 — batch_bb0c1ba0 succeeded 70/70 while log said "140 failed"*

#### ~~Confirm batch-poller sees ECS-submitted batch jobs (poller visibility)~~ ✅ Done + verified (keys match, tested)
Resolved: the ECS task writes `batch_job#{batch_id}` (status=pending) via
`src/utils/job_queue.enqueue_job` to `CACHE_TABLE`, and
`lambda_handlers/batch_poller._get_pending_jobs` scans the SAME table on
`begins_with(cache_key, "batch_job#")` with status pending/ready. The
`metrics#{batch_id}` record (`grok_client.py`) the TODO flagged is a SEPARATE
metrics record, not what the poller keys on. `ecs_entrypoint` enforces routing
through submit-only (anti-orphan guard: alerts if the enqueue/read-back is
absent). Tested: `tests/unit/test_batch_poller.py` seeds `batch_job#{id}` and
asserts the poller finds→marks ready→triggers retrieve (19 passing w/
test_job_queue); `tests/test_batch_routing_guard.py` guards the submit-only path.
*Source: St. Vith 2026-09-27; verified 2026-10-01*

#### Delete stale legacy `chunk-NNN` OCR dirs superseded by re-OCR
Re-OCR runs (off-by-one fix) write new page-range chunk dirs
(`chunk-p0001-0050`, …) but leave the **old pre-fix `chunk-000`..`chunk-NNN`**
dirs in place. The merge correctly **skips** them ("legacy chunk dir without
page range — cannot map to physical pages"), so output is not polluted, but the
stale dirs are confusing clutter and could trip tooling that globs `chunk-*`.
Delete the superseded legacy dirs from
`s3://dev-wwii-data-pipeline/ocr-output/{source}/` after a re-OCR is verified.
Affected so far: `stvith_boyer_full` (chunk-000..005), and `ETO_Order_of_Battle`
(chunk-000..019) once it is re-OCR'd. Mildly destructive (S3 rm) — verify the
new page-range chunks + merged output first.
*Source: stvith_boyer_full re-OCR 2026-09-26*

#### ~~Batch ALL entity types, not just events~~ ✅ Done + verified
All core extractors route Grok calls through `grok_client.extract_json` →
`chat_completion`, which in batch_mode collects into the batch collector and
raises `BatchModeCollecting` — so dates/places/people/groups
(`src/extraction/batch_parallel._batch_extract`) AND events
(`src/extraction/events.extract_events`) all batch, and `phase2_extract.main`
runs a second batch cycle for the optional extractors
(weather/equipment/logistics/casualties/supplemental). Empirically confirmed
2026-10-01: invoking `extract_dates_batch_async` + `extract_people_batch_async`
with `batch_mode=True` made NO live call and collected 2 requests
(cache_types ['dates','people']). The TODO premise (only events batch) is stale.
*Source: Ardennes 2026-06-13; verified 2026-10-01*

#### ~~Bibliography resolver processes duplicate citations redundantly~~ ✅ Fixed 2026-09-27
Same citation referenced by multiple sub-events is processed N times (NARA identify + search). Cache prevents duplicate API calls but generates log noise (4x identical log lines). Fix: deduplicate by citation text before resolution loop.
**Done:** `resolve_bibliography_dir` now dedups by citation key (verbatim, else
author|title); identical citations resolve once and reuse the result (`deduped`
stat). Tested in `tests/test_bibliography_resolver_guard.py`.
*Source: Phase 3 log observation 2026-06-16*

#### ~~Narrative content misclassified as document_reference reaches NARA resolver~~ ✅ Fixed 2026-09-27 (guard)
Footnotes with factual narrative (e.g., "In October 1941, the Germans had discussed...") are being sent to NARA identification. Two fixes needed: (1) improve supplemental.yaml classification prompt to better distinguish narrative from citations, (2) add guard in bibliography_resolver to skip text that doesn't match citation patterns (no author/title/date structure).
**Done (guard, #2):** `_looks_like_citation` gates the resolvers — prose with no
bibliographic structure is marked `not_citation` and never sent to NARA
(preserves legitimate NARA lookups: any RG/archive-ref/structured entry still
resolves). **Still open (#1):** tighten `supplemental.yaml` classification prompt
to reduce misclassification upstream (defense in depth).
*Source: Phase 3 log observation 2026-06-16*

#### Bibliography human-disposition UI (review_queue.json)
`resolve_bibliography_dir` now writes `review_queue.json` — real citations that
could not be grabbed online (per the "grab what's legitimately online, human-
disposition the rest" intent). There is **no UI** to work that queue yet. Build a
disposition UI analogous to the dedup UI + OCR markdown-review UI: show the
structured citation + verbatim, let a human mark disposition (request-from-
archive, manual link, mark-not-a-source, etc.). **NAT: treat as an async human-
review gate like dedup + mdreview** (tear down while waiting, resume on
completion). Deploy alongside the other UIs (same ComputeStack deploy-path
caveats).
**Retrieval mechanism now exists (2026-09-27):** `src/enrichment/source_retrieval.py`
downloads legitimately-online resolved items into a **quarantine** area
(`bibliography/retrieved/pending_review`, deliberately outside content/ so Phase 1
can't auto-ingest), gated on the existing `config/domain_blacklist.yaml` (NOT a
strict allow-list — download unless blacklisted; human review decides import).
Marks entries `retrieved_pending_review` and **never auto-processes** them.
Legitimacy basis: the pipeline downloads to AI-summarize (Grok) + attribute, not
republish. The UI needs to: drive retrieval for queued/resolved items, show the
downloaded content, and let a human accept (→ move into processable corpus) or
reject. Mechanism is staged/tested but intentionally NOT auto-wired to a phase.
*Source: bibliography-resolution intent discussion 2026-09-27*

#### Archive.org metadata + legitimacy enrichment (free, no new key)
The resolver already calls the Archive.org Metadata API (`/metadata/{id}`) but
only extracts PDF url + page count. From the **same call** we can also capture
**publisher, ISBN, date, creator, rights/license** (actionable publication data
for the professional-historian audience even when not downloadable) and check
`access-restricted-item`/rights so only **legitimately** public items are
presented as "grabbed online" — lending-only/restricted ones route to the
human-disposition queue. Also consider OpenLibrary (book metadata) and Crossref
(journal DOIs) as free authoritative metadata sources. Additive to the resolver;
ties into the existing "Amazon metadata enrichment" future item.
*Source: bibliography-resolution intent discussion 2026-09-27*

### Prompts & LLM Integration

#### ~~Prompt versioning (cache invalidation)~~ ✅ Fixed
Cache key hash now includes system_prompt. Any YAML change (prompt or system prompt) auto-invalidates.

---

## Low Priority (code quality, minor improvements)

#### Finish ecs_entrypoint.py → ecs_modules/ extraction
2473 lines. Proposed splits: `notifications.py` (~110 lines), `dedup.py` (~280 lines), `locks.py` (~150 lines). Would reduce entrypoint to ~1900 lines.
*Source: QA radon 2026-06-13*

#### Consolidate find_duplicate_*.py scripts
Strategy pattern or shared base would cut ~40% code.
*Source: QA review 2026-06-13*

#### Refactor phase3_enrich_data.py:main (D(23) complexity)
*Source: QA radon 2026-06-13*

#### Fix `.gitignore` trailing newline (black already clean)
`black --check` passes repo-wide (427 files, verified 2026-10-01) — the "6
scripts" half is DONE. Remaining: `.gitignore` has no terminating newline on its
last line (`tmp/`); add one.
*Source: Code review 2026-06-13; black-half verified done 2026-10-01*

#### Reduce image memory usage in equipment.py
*Source: CODE_REVIEW.md*

#### DynamoEntityStore.query_unenriched does full table scan
Add GSI on `entity_type` + `enrichment_status`.
*Source: CODE_INTEGRITY_REVIEW.md #14*

#### Per-book queue ordering not guaranteed
DynamoDB scan order is undefined. Low impact (sequential processing, just affects which book goes first).
*Source: Review 2026-06-14*

#### Hard-coded step lists in Phase 3 notifications
`_build_phase_section` and `_update_lock_status` duplicate the enrichment step list. Could drift.
*Source: Review 2026-06-14*

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Evaluate residential proxy for OpenSERP
Tailscale exit node preferred. Free, no third-party trust.
*Source: end-2-end-1 Phase 3 observation*

---

## Regression Protection

#### Smoke test CI job — full pipeline on chapter99
Run Phase 1→2→3 end-to-end with real file I/O on test chapter. Catches multi-phase coordination bugs that unit tests miss (threading, file locks, phase transitions).
*Source: QA regression review 2026-06-15*

#### Thread-safety stress test (50 threads, @pytest.mark.slow)
Current `test_event_mention_race.py` uses 10 threads. Add variant with 50 threads for CI only. Race conditions often only manifest under load.
*Source: QA regression review 2026-06-15*

#### Expand prompt-schema contract tests to all 27 YAMLs
Assert every YAML has valid `prompt_template`, `schema` (parseable JSON), `system_prompt`. Verify all `{placeholders}` in templates have matching function arguments. Currently only events + 5 types covered.
*Source: QA regression review 2026-06-15*

#### Phase coordination integration test
Mock 2 concurrent phases and verify: Phase 3 can't tear down NAT while Phase 2 lock held, per-book queue processes correctly, lock status updates at each step.
*Source: QA regression review 2026-06-15*

#### Snapshot/golden tests for merge output
If `_merge_person`, `merge_generic`, or `update_event_refs` change behavior, output could silently drift. Add golden file assertions: input A + B → expected merged C.
*Source: QA regression review 2026-06-15*

#### Enforce mypy --strict on new files only
Prevent `find_related_groups.py`-style issues from accumulating. Don't enforce on legacy but require new code to pass strict mode.
*Source: QA regression review 2026-06-15*

---

## Future / Research

#### Explore removing DynamoDB as a general data store (esp. Phase 3 bulk transfer)
Investigate whether DynamoDB is still earning its place, and scope removing it as
a *general* data store. Code review (2026-09-30) found it plays three distinct
roles — they must be evaluated separately, not lumped together:

> **Storage review 2026-10-02** (`docs/current/STORAGE_REVIEW.md`) decided the
> role-split: remove the entity *materialization* copy (→ Postgres with the RAG
> phase), **preserve** the concurrency-safe `merge_entity`, **keep** the tiny
> coordination KV (locks/`pending#*`/NAT leases/`batch_job#`) on DynamoDB
> long-term. pgvector single-store AFFIRMED but deferred to the post-ingestion
> RAG phase. Also tracked there: **S3 `output/` JSON consolidation** (45k
> per-entity objects → per-type NDJSON/Parquet) — an efficiency/cost
> optimization, NOT a correctness fix; do when Phase-3 bulk-read time is a felt
> bottleneck OR when the Postgres load is built, whichever first.

1. **Bulk Phase 3 materialization (weak — prime candidate for removal).**
   `s3_sync._materialize_from_dynamo` calls `DynamoEntityStore.list_all` once per
   entity type = **11 sequential full-table `Scan`s** (`FilterExpression`
   `begins_with(cache_key, "entity#<type>#")`), deserializing every entity's
   `data` blob. A filtered `Scan` reads/bills the whole table then discards
   non-matches — the same "crawl everything" anti-pattern as the S3
   `list_objects_v2` + per-object `download_file` loop it's meant to beat, so it
   is unlikely to be a real bulk-transfer win. **This is the "S3 is slow on Phase
   3" path the removal question is really about.**
2. **Small coordination (KV-shaped, legitimately good on DynamoDB).** Manifests
   (`manifest#phase2`, `pending#parsed`) and the Phase 3 lock/status
   (`_update_lock_status`). Tiny; keep unless the whole store goes.
3. **Concurrency-safe entity merge (genuinely earns its keep).**
   `merge_entity` / `store_bibliography` use version-conditional writes so two
   books extracting the same entity (e.g. "Eisenhower") don't clobber each
   other's `event_mentions`. Comments note this replaced flock-guarded S3 JSON
   writes that were per-host, NOT cross-host safe. Removing this without a
   replacement reintroduces that race — see "True multi-job concurrency" and
   `CONCURRENCY_AND_NAT_SPEC.md`.

**Fix options for the Phase 3 transfer bottleneck (cheapest first), to benchmark
before deciding:**
- (a) **Parallelize the S3 transfer** — current `s3_sync` loop is serial
  `download_file`; use concurrent downloads / `aws s3 sync`. Lowest-risk quick
  win, zero architecture change; may erase the pain that motivated
  `_materialize_from_dynamo`.
- (b) **Consolidate into few large objects** — write one Parquet/NDJSON blob per
  entity type so Phase 3 pulls ~11 objects instead of thousands (attacks the
  actual cause: object count; Parquet adds columnar projection).
- (c) **If DynamoDB materialization stays, stop using `Scan`** — add a GSI on
  `entity_type` so `list_all` becomes a `Query`. (Overlaps the existing Low-Prio
  item "DynamoEntityStore.query_unenriched does full table scan".) Weaker than
  (a)/(b).

**Decision inputs to gather (not yet done):** measured Phase 3 timings +
corpus/object counts, DynamoDB-scan vs. parallel-S3 vs. Parquet crossover, and a
plan for preserving role #3's concurrency safety (Postgres row-level
locking/`ON CONFLICT`, or keep a minimal Dynamo lock table) if the general store
is removed. Ties into the Aurora/pgvector direction ("Postgres dialect adapter
for src/loader") — the eventual query store is Postgres, so DynamoDB here is
really ingestion-time coordination, not a query store.
**Status:** exploration only — nothing to implement yet (per owner 2026-09-30).
*Source: DynamoDB value review 2026-09-30*

#### OCR structural fidelity: raise render DPI + evaluate augmenting Chandra
Chandra runs at the 192 render-DPI baseline (`chandra/settings.py:IMAGE_DPI`,
not exposed on the CLI); its own benchmark recommends 300. Measured: 192→300 =
+83% usable detail (3.41→6.24 MP the model actually sees); the
`scale_to_fit(max_size=(3072,2048))` ceiling caps gains above 300. **Do:** (1)
empirically diff 192-vs-300 re-OCR of the hard St. Vith pages (p155 2-D
task-org table, p103 casualty table) via `tmp/dpi_probe.py`; if 300 helps, make
it the corpus-wide default (override the library setting — no CLI flag exists).
(2) For the persistent structural blind spots that DPI won't fix (2-D task-org
flattening, block-quote markup loss), evaluate augmenting Chandra with a
dedicated table engine — **PaddleOCR PP-StructureV3** (Apache-2.0, table
structure + multi-column reading order) is the first candidate; Docling as a
cross-check; ensemble-disagreement → `needs_review`. Full analysis in
`docs/current/dataquality/CHANDRA_OCR_DESIGN.md` (Render DPI / Augmenting
Chandra sections).
*Source: OCR fidelity investigation 2026-09-22*

#### Postgres dialect adapter for src/loader (Aurora target)
The `output/` → relational loader (`src/loader/`) currently runs **SQLite only**,
though its docstring claims "works with any PEP-249 connection ... psycopg
against Aurora." Three SQLite-specific spots block Postgres: `create_schema`
uses `conn.executescript` (not in psycopg), `_insert` uses `INSERT OR REPLACE`
(Postgres needs `ON CONFLICT (pk) DO UPDATE`), and `?` placeholders (psycopg
uses `%s`). `transform.py` is already DB-agnostic, so this is a contained seam,
not a rewrite. Also missing: `schema_pg_extras.sql` (pgvector/PostGIS/HNSW),
referenced in a comment but not on disk. Defer the full adapter until the Aurora
target is provisioned (untestable before then) and the embedding dimension is
settled (`content_chunks VECTOR(?)` depends on the chosen embedder). **Done
(2026-09-22):** `load.py` docstring corrected to "SQLite today; Postgres via a
planned dialect adapter," and a `_require_sqlite` guard now rejects non-sqlite
connections with a clear `NotImplementedError` (tested). Remaining: the adapter
itself + `schema_pg_extras.sql`.
*Source: loader schema-gap review 2026-09-22*

#### Reprocess already-imported data after Phase 0 routing lands
Once the format-agnostic ingestion + media classification work is built (see
`docs/current/dataquality/STRUCTURED_DATA_ROUTING.md`), build a specialized
audit-and-reimport script that:

> **Status (2026-09-18):** the Phase 0 ingestion front-end + OOB scanned-table
> parsers are now built (`src/ingestion/`, see
> `docs/current/dataquality/INGESTION_FRONT_END.md`). This reprocess/backfill
> script remains pending and should run once Phase 0 is wired as a runnable
> phase and the entity-convergence bridge lands.

1. **Audits existing output** — scans already-extracted content and entities for
   sources affected by the pre-Phase-0 gaps: embedded image-maps that were lost
   between `images.py` and `maps.py`, images misclassified by keyword-only
   `_classify_content_type`, images dropped by `alt_text` dedup, and any
   sources that never parsed (raw PDFs sitting in `contentrepository/`).
2. **Reports** what would change (per source: images recovered, maps
   reclassified, entities newly created) before touching anything.
3. **Re-attempts import** through the finished Phase 0 pipeline, feeding results
   into dedup so recovered media/entities merge with existing ones rather than
   duplicating.
Applies across ALL sources (not ibiblio-specific) — the underlying fixes are
made once in the shared parser/media path, and this script backfills everything
imported before the fix.
*Source: Structured-data routing analysis 2026-06-30*

#### Amazon metadata enrichment for confirmed books
For bibliography entries confirmed as published books, search Amazon.com for metadata: book cover image, ISBN, edition info, page count, publisher details. Supplements Archive.org/Gutenberg data with commercial metadata.
*Source: Search debug session 2026-06-17*

#### True multi-job concurrency
Multiple books in parallel. Requires per-book locking, shared DynamoDB entity store, dedup coordination.
**Spec written 2026-09-27:** `docs/current/dataquality/CONCURRENCY_AND_NAT_SPEC.md` — per-book locks, reference-counted NAT, limit-aware dispatcher pool (Grok rate + Fargate vCPU as binding limits), global dedup barrier, shared-entity-store hardening. Motivated by the WWIIArchives backlog (628 files / ~502 GB).

#### Grok function calling for Phase 3 enrichment
Blocked on: function calling support in batch API.

#### Step Functions pipeline orchestration
Replace SNS→SQS→Lambda→ECS with Step Functions.

#### New entity types: Economic Data, Policy/Legislation
Prompts and schemas drafted in `docs/archive/2026-10-11/dataquality/new_entity_types.md` (archived).

#### UK National Archives (Discovery API)

#### Model routing expansion (grok-3-mini for simple extractors)
Dates, places, weather, casualties, logistics could use cheaper model. Needs A/B testing with quality metrics before rolling out.
*Source: Prompt deep dive 2026-06-14*

#### Prompt version in cache key
Enables safe prompt iteration without manual cache invalidation.
*Source: Prompt deep dive 2026-06-14*

#### Cost quantification for model_map expansion
Token counts and per-run cost estimates needed for ROI analysis.
*Source: Kiro evaluation 2026-06-14*

#### Retry escalation (cheap model → expensive model on validation failure)
Undefined policy for when grok-3-mini fails validation. Retry same or escalate?
*Source: Kiro evaluation 2026-06-14*

---

## Completed (2026-06-15)

#### ~~BatchCollector thread safety~~ ✅ — `threading.Lock` added to `.add()`
#### ~~DynamoDB dual-write reconciliation~~ ✅ — wired into `_materialize_from_dynamo`
#### ~~Supplemental_Materials key normalization (all paths)~~ ✅ — 5 entry points normalized
#### ~~Bibliography resource_urls array~~ ✅ — prompt + sanitizer + code aligned
#### ~~Event mention dedup~~ ✅ — checks (EventID + book + Sub_event_Name)
#### ~~merge_generic cross-ref updates~~ ✅ — `update_event_refs` + targeted JSON replacement
#### ~~Optional extractor idempotency~~ ✅ — `.processed_events.json` markers
#### ~~Phase results zero-count warning~~ ✅ — logs + SNS notification
#### ~~BatchModeCollecting explicit catch~~ ✅ — DEBUG in all 4 optional extractors
#### ~~Truncation recovery~~ ✅ — `GrokTruncationError` + `extract_with_chunk_halving`
#### ~~update_event_refs corruption~~ ✅ — `_replace_id_in_obj` (recursive, field-targeted)
#### ~~Phase 1 chunk overlap~~ ✅ — 3-paragraph overlap between splits
#### ~~System prompt centralization~~ ✅ — all use `get_system_prompt(name)` from YAML
#### ~~All prompts externalized~~ ✅ — 27 YAML files, hard fail on missing
#### ~~EntityCreatedTopic~~ ✅ — removed (redundant, dead handler cleared)
#### ~~Enrichment started notification~~ ✅ — SNS after downloads, before API calls
#### ~~Stalled batch notification~~ ✅ — after 4h, hourly, with progress n/n
#### ~~Secrets scanning in deploy~~ ✅ — gitleaks/detect-secrets, blocking
#### ~~Scripts portability~~ ✅ — ENV_NAME/AWS_DEFAULT_REGION
#### ~~Orchestration tests~~ ✅ — 14 tests (locks, sync, threads, loaders)
#### ~~Prompt schema alignment expanded~~ ✅ — 29 tests, output schema validation
#### ~~BackgroundSync skips enriched files~~ ✅ — `_downloaded_keys` tracks mtime, re-uploads modified
#### ~~Search queries externalized~~ ✅ — `search_queries/*.yaml` + loader + deploy validation

---

## Completed (2026-06-13 through 2026-06-14)

#### ~~additionalProperties: false drops casualties/equipment~~ ✅ Fixed
Added CasualtyID, date_string, impacted_* to CASUALTY_ITEM_SCHEMA. Added 8 fields to PEOPLE_GROUP_ITEM_SCHEMA.

#### ~~Supplemental_Materials key mismatch (primary path)~~ ✅ Fixed
Prompt updated to singular, `sanitize_supplemental_data()` normalizes plural→singular.

#### ~~index.json race condition~~ ✅ Fixed
Per-directory `threading.Lock` wraps read-modify-write. `_build_date_id_lookup` outside lock.

#### ~~BackgroundSync uploads partially-written files~~ ✅ Fixed
Skips `.tmp` files + requires mtime stable >2s before upload.

#### ~~DynamoDB dual-write silent failures~~ ✅ Partial
Upgraded to WARNING, added `_track_failed_write` tracking. Reconciliation not yet wired.

#### ~~Phase 3 no-op on review-all-data~~ ✅ Fixed
`force_reprocess` now skips DynamoDB materialization, downloads full S3 corpus.

#### ~~Phase 3 tears down NAT while Phase 2 running~~ ✅ Fixed
`_teardown_networking` and `_stop_openserp_if_running` check for other phase locks before tearing down.

#### ~~NAT guardrail kills networking during long Phase 3~~ ✅ Fixed
openserp_manager checks `_any_lock_held()` before force teardown.

#### ~~EIP leak from overnight bounce loop~~ ✅ Fixed
`_create_nat` releases orphaned EIPs before allocating. Fresh EIP each time (reputation isolation).

#### ~~Reconciliation launches Phase 3 with no book (network thrash)~~ ✅ Fixed
Only relaunches if `pending#enrich#*` queue has entries. Uses book name from queue.

#### ~~Per-book queues (Phase 2 + Phase 3)~~ ✅ Implemented
`pending#parsed#{book}`, `pending#enrich#{book}`. Networking stays up between books.

#### ~~Prompts externalized to YAML~~ ✅ Done
people_groups, casualties, logistics, weather, biography — no inline fallback, fail on missing YAML.

#### ~~Deploy-time prompt validation~~ ✅ Done
11 YAML files validated (existence, template, schema JSON) — blocks deploy on failure.

#### ~~Phase 3 lock status + book name~~ ✅ Done
Lock item includes `book` and `status` fields. Phase 3 updates status at each step.

#### ~~Download progress logging~~ ✅ Done
Every 500 files with elapsed time. Total count shown upfront.

#### ~~Cache hit periodic logging~~ ✅ Done
Every 100 hits at INFO level.

#### ~~Trivy blocking + pip-audit fallback~~ ✅ Done
Deploy fails on HIGH/CRITICAL vulnerabilities. Podman support added.

#### ~~Entity validation skip list~~ ✅ Fixed
`index.json`, `duplicate_report.json`, `not_duplicates.json`, `not_people.json`, `not_related.json`, dotfiles excluded from entity schema validation.

#### ~~phase3_enrich_data.py missing `import json`~~ ✅ Fixed

---

## Completed (2026-06-09 through 2026-06-13)

_(See git log for details — 23 items including Phase transitions, batch states, empty guards, prompt fixes)_

---

## Completed (2026-06-03 through 2026-06-07)

_(See git log for details — 30+ items including DynamoDB entity store, incremental dedup, cost optimizations)_


---

## Completed data-quality program (2026-10-10 / 2026-10-11)
_Moved from the HIGH blocks above once merged. Prevention + detection layers complete; (a) remediation + (b2) corrective reprocessor remain OPEN above._

### HIGH-1 cross-reference + content-quality program (items 1/2/4/4b)
1. **Cross-reference integrity — root cause + fix (NOT N separate extractor bugs).** The audit
   (**[TOOL DONE 2026-10-10]** `scripts/referential_integrity_audit.py`, read-only, tested; corpus
   run → 5,076 dangling refs; report in `docs/current/dataquality/referential_integrity_report.json`)
   surfaced the dangling edges (maps→events 100%, casualties→impacted_places 86%, weather→places 48%,
   casualties→events 40%, images→events 13%, source_section→events 7%). INVESTIGATION (2026-10-10)
   found these are NOT per-extractor logic bugs — the extractors write the right reference at write
   time. Two real causes:
   - **(i) Staleness/ripple:** EventIDs come from the LLM response and are NON-DETERMINISTIC per run.
     Events re-extracted Sept 2026 got fresh ULIDs; dependents written Apr–Jun (maps/weather/
     casualties/images) still carry the old IDs → dangling. Any post-write event-ID change ripples
     to every referrer.
   - **(ii) Live write-time bug:** FRESH source_sections (written today) reference EventIDs that
     resolve to NO event (`source_section→events` 11/157, all recent) — a dangling ref created at
     write time because nothing validates that a reference RESOLVES (only the record's own schema is
     checked). The LLM-supplied EventID is not validated against a real event.
   **FIX (both in CORE code, not throwaway utilities):**
   - **#1 Deterministic EventIDs** — derive the EventID from stable content (book + chapter +
     sub-event) instead of a volatile LLM/ULID value, so re-extraction yields the SAME id (no
     staleness, no ripple). Where the LLM supplies an id, VALIDATE it (reject hallucinated/
     non-resolving). Design-first (schema/migration implications).
   - **#2 Enforced referential-integrity check across ALL json objects** — reference fields must
     resolve to an existing target; enforce in core (write-guard reference check and/or a standing
     pipeline corpus pass), handling the same-run create-order case (an event + its referrer written
     in one run). The audit tool is the DETECTIVE/reporting half; this is the ENFORCEMENT half.
     ROOT CAUSE of the live source_section→events bug (confirmed 2026-10-10): a dependent
     (source_section) was persisted referencing an EventID whose EVENT RECORD WAS NEVER PERSISTED
     (the event write was blocked by the schema guard, but source_section was still built from the
     in-memory event dict + written) → a dangling ref born at write time.
     **[DONE 2026-10-10]** Implemented (branch `feature/referential-integrity-enforcement`):
     core module `src/utils/referential_integrity.py` — EDGES + PK targets are now DERIVED FROM
     `entity_registry`/schemas (NOT hand-listed), so coverage is EVERY schema-declared PK-named
     reference field across ALL entities (17 edges, 12 PK targets) and cannot drift (drift-guard
     tests assert full coverage). `enforce()`/`run_for_phase()` run at the end of Phase 1/2/3
     (centralized, fail-safe, SNS alert = aggregate counts only, raw-ID samples opt-in default-OFF).
     `events.py:_save_event_output` now gates the source_section emit on the event write persisting
     (fixes the live write-time dangling bug). CLI `scripts/referential_integrity_audit.py` is a thin
     wrapper over the same core (single source). Full gate PASS; code+security sub-agent reviewed.
     REMAINING PRECISION GAP — **[RESOLVED 2026-10-10, Fix A]:** the `casualties.impacted_*` lists
     now declare their resolved reference id (`PersonID`/`PlaceID`/`EquipmentID`/`LogisticsID`/
     `PeopleGroupID`) under the object branch, so the schema-derived checker discovers them; the
     `_walk_id_fields` walker was also fixed to handle union `type` (e.g. `["array","null"]`), which
     had silently hidden EVERY nullable-array reference field — coverage went 17 → 45 edges, incl.
     `people.biographical_profile.group_affiliations[].GroupID` (the "absent" person→group edge was
     real, just nested under a nullable array). `PeopleGroupID` is aliased to the groups key-space.
   NOTE: a one-off remediation of the current stale TEST data is intentionally SKIPPED — dev/test
   data will be refreshed from prod; the only durable value is recurrence-safety in a future prod
   release, which #2-in-core provides (a throwaway cleanup script would die at the next data refresh).
   **#1 Deterministic EventIDs — [DONE 2026-10-10, PRs #325 + #326 merged to main]:**
   central `src/utils/deterministic_ids.py` (`event_id`/`sub_event_id`) derives a schema-valid ULID
   from stable content (book+chapter+`section_id` / parent+paragraph-range), replacing the volatile
   LLM-supplied id so re-extraction is idempotent (no ripple). Wired into `events.py` (both paths)
   + the supplemental notes-event; dependents inherit the id (Phase 3 mints none — read-only).
   Sub-agent review blockers FIXED + verified on main: (a) reads the REAL `section_id` key (the
   `section` typo would have collapsed 84% of chapters onto one EventID); (b) nameless/rangeless
   sub-event uses a deterministic positional fallback instead of raising before validation.
   Holistic post-merge review CRITICALs also fixed (PR #326): list-valued artifact no longer aborts
   the pass; nested local event files are listed (were invisible → mass false-positive); the
   underscore `Sub_eventID` edge alias; chunk-merge + notes Sub-eventID collision disambiguation.
   **NON-NEW-EVENT RESOLUTION (resolve-before-create — added 2026-10-10 per owner):** any path that
   ENCOUNTERS an event (now or future) must RESOLVE to the existing event, never mint a new/duplicate
   one. Deterministic EventIDs give this for free on re-extraction (same content → same id → same
   record). Confirmed today: Phase 3 enrichment mints NO events (read-only consumer) and the
   dependents only READ `Event.EventID`, so there is no current duplicate-event path. REQUIREMENT to
   PRESERVE: if a future phase/feature ever creates events (e.g. a maps/vision branch, a corrective
   reprocessor), it MUST compute the deterministic id for the source unit and resolve-or-attach to the
   existing event rather than wrapping/creating a parallel one — this is the event-level analogue of
   the entity resolve-before-write rule in the corrective-reprocessor acceptance criteria below.
   **HOLISTIC-REVIEW FOLLOW-UPS (deferred from the 2026-10-10 merged-code review; CRITICALs already
   fixed — list-valued-file guard + recursive event listing + `Sub_eventID` alias):**
   - (self-type refs) `_discover_edges` excludes a field whose leaf == the entity's own PK GLOBALLY,
     so a nested cross-reference to ANOTHER record of the same type (e.g.
     `equipment.related_equipment[].EquipmentID`, a future `related_places[].PlaceID`) is silently
     skipped. Exclude own-PK only at the record ROOT (path==""), not for nested list items.
   - (uncovered group ref) `people.biographical_profile.primary_group_id` is a real group reference
     but its leaf name is not `*ID`-cased (it's `primary_group_id`), so `_walk_id_fields` does NOT
     discover it as an edge — it is unenforced. Fix: rename to a `*GroupID` form, or add a name
     alias. Related consistency invariant worth a FLAG validator: `primary_group_id` SHOULD equal
     one of the `group_affiliations[].GroupID` / `units_served[].GroupID` on the same person (people
     can belong to multiple groups — one primary scalar + many affiliations/units arrays).
   - (perf) `enforce()` is O(corpus): `build_pk_sets` reads every entity+event file, then `audit`
     re-reads each source entity dir once PER edge. Cache records per dir once + iterate edges in
     memory; consider skipping the Phase-1 enforcement run (no cross-entity refs yet = pure cost).
   - (ECS timing) ensure end-of-phase enforcement runs AFTER the final S3 sync (or points at the
     local workdir), else not-yet-uploaded files read as false dangling.
   - (centralize path truth) `referential_integrity` re-derives entity dirs / event nesting instead
     of using the registry's `output_glob` (the root cause of the local nested-event bug). Fold
     path/nesting knowledge onto `output_glob`.
   - (consistency) `supplemental._apply_defaults`/`sanitize_supplemental_data` still mint
     `str(ulid.new())` EventID/Sub-eventID as degenerate null-fallbacks — volatile, bypassing the
     central deterministic helper; derive from context or document why.
   - (storage-root consistency) phase3 passes `args.output_dir` while phase1/2 pass
     `paths["output_root"]`; CLI builds storage via `create_storage` while `run_for_phase` hardcodes
     `LocalStorage` — confirm all resolve to the same corpus root.
2. **Semantic validators as FLAGS (not hard blocks)** — **[IMPLEMENTED 2026-10-10]:** central
   `src/utils/semantic_flags.py` (`flags_for`/`annotate`, flag-not-block, fail-safe, recompute+replace
   so stale flags clear) writes advisory `quality_flags`: `geo_missing_coords`/`geo_impossible_coords`
   (places — GLOBAL validity, NO theater box: WWII is a global conflict + the corpus will grow
   worldwide, so a regional box would false-flag by design; only null/0.0/physically-impossible
   coords are flagged), `date_out_of_window`/`date_unparseable` (dates), `citation_stub`/
   `citation_unresolvable` (bibliography). Wired into BOTH central write paths (`_validate_entity` +
   `locked_json.save` via a shared `_annotate_semantic_flags` helper). New optional
   `QUALITY_FLAGS_PROPERTY` on places/dates/bibliography (+ version bumps + fingerprint pins).
   Code+security sub-agent reviewed; full gate PASS. Design:
   `docs/current/dataquality/SEMANTIC_FLAGS_DESIGN.md`.
   BASE RATE for the (future) catcher: with the GENEROUS config-driven date window (default
   1930–1950, incl. WWII antecedents Abyssinia/Spanish-CW/Sino-Japanese/Anschluss/Khalkhin-Gol +
   late-1940s aftermath; override via `processing.date_window` / env) `date_out_of_window` fires
   only on genuinely implausible dates. NOT-YET-WIRED: forwarding flags to the statistical catcher
   as an `allow_flagged` dimension (future work, ties to item 4).
4. **Teach the statistical catcher to see low-information ALLOWS** — **[DONE 2026-10-10]:**
   `validation_stats` now records `allow_empty` (populated content-field count below a per-entity
   floor — people/places/bibliography/equipment/dates=2; metadata/PK/quality_flags/0.0-coords not
   counted) and `allow_flagged` (one datapoint per HIGH-2 `quality_flags` code, keyword constrained
   to the known vocabulary for PII-safety). `systematic_clusters()` generalized to the concern set
   {block, allow_empty, allow_flagged} at the same count≥10/rate≥5% thresholds, so a book-wide
   empty-biography or `geo_missing_coords` cluster alerts like a block cluster (outcome-aware log +
   SNS body). Wired into the central guard (`_run_schema_validation` allow + allow_warn branches,
   respecting `record_stats` so pre-checks don't double-count). This closes the catcher's biggest
   blind spot AND completes the HIGH-2 flag→catcher forwarding. Fail-safe; code+security reviewed;
   full gate PASS. Design: `docs/current/dataquality/CATCHER_LOW_INFO_FLAGGED_DESIGN.md`.
4b. **Extend the guard to PHASE 1 (parse) output** — **[DONE 2026-10-10]:** new content schema
   `src/schemas/parsed_output.py` (backbone: book + chapter_title + paragraphs[absolute_number,text];
   additionalProperties permissive; NOT in ENTITY_REGISTRY — no PK/dedup/cross-ref applies to parse
   artifacts). Shared `file_lock.guard_parsed_content()` validates shape (BLOCKS a garbage parse
   before Phase 2) + flags an empty parse (`allow_empty`, still written — plate pages are
   legitimate), recording block/allow/allow_empty to the catcher under entity `parsed` (rolls into
   the same systematic-cluster alert path). Used by BOTH the local writers
   (`write_parsed_with_guard`, atomic tmp+replace) AND the S3/Lambda production path
   (`lambda_handlers/phase1_handler.py` — the review-caught bypass, now routed); a blocked chapter
   logs `DROPPED ... re-parse needed`, not "Saved". Fail-safe (validator/import error still writes).
   Code+security sub-agent reviewed; full gate PASS (1974 tests). Design:
   `docs/current/dataquality/PHASE1_PARSE_GUARD_DESIGN.md`.

### Detective/bypass-prevention (b) + consistency (c)/(d)
- **(b) Detective layer + bypass prevention.** (1) **S3-event Lambda backstop**: on `ObjectCreated`
  under `output/`, run the SAME `_validate_entity` primitive (no reimplementation — avoid guard
  drift); on invalid, alert (phase2-complete SNS → Slack/email) + quarantine. Fail-LOUD.
  **SYNC REQUIREMENT (acceptance criterion):** the Lambda MUST run the repo's live validation code,
  never a copy. `update_lambdas.sh` already bundles `src/` into the Lambda zip, so the primitive is
  shared by construction — but add a DRIFT CHECK to `check_component_versions.sh` (the deploy
  preflight): hash `src/schemas/` + `src/utils/file_lock.py` + `src/utils/validation_stats.py`,
  compare repo-HEAD vs the deployed Lambda bundle, WARN (or `--strict` fail) on mismatch so a
  partial deploy can't leave the validation Lambda behind the pipeline. **Quarantine = tag-in-place
  (`quarantine=true`) + bucket-policy DENY reads to consumer roles, NOT a key move** (moving
  orphans the unique cross-ref mentions the bad fragments hold); lifecycle-expire the quarantine
  tag/prefix. (2) **grep gate** in `scripts/gate.sh` forbidding new raw `json.dump`/`write_text` to
  `output/` entity dirs, so the next contributor's bypass fails CI. (3) **corpus-level audit job**:
  periodic scan running the guard across all of `output/` + a referential-integrity check (every
  cross-ref ID resolves) + ID-uniqueness + dangling-mention + dedup-consistency checks.

- **(c) Minor:** make `write_json_with_lock` return a bool (did-write) so `merge._write_entity_guarded`
  consumes it directly instead of the mtime/exists heuristic; consider moving `_validate_entity` to
  `src/schemas/write_guard.py` if the single-writer-facade refactor happens.
- **(d) Consistency — unify the second validation source.** `src/utils/schema_registry.py`
  (`SchemaRegistry`/`get_registry`) is a SEPARATE validator stack used by the READ/report tools
  (`scripts/validate_data.py`, `validation_report.py`, `generate_dashboard.py` + several tests). It
  validated against `src/json_schemas` — a DIFFERENT (and STALE, pre-refactor event-nested) schema
  source than the write guard's `ENTITY_REGISTRY`/`src/schemas`, so the report tooling judged
  current per-entity records against a model they no longer match.
  **[DONE 2026-10-10]** `SchemaRegistry` now serves the AUTHORITATIVE schemas (resolved via
  `entity_registry`/`src.schemas` — the same ones the write guard enforces), keeping the legacy
  singular aliases (`event`/`place`/`date`/`map`) so the CLIs/tests still work. Read-time and
  write-time validation are now provably consistent. Fixture + integration tests updated from the
  stale `{name:[records]}` wrapper to authoritative per-entity records (and fixed a stale
  equipment-fixture `name`→`common_name`). NOTE (scope): `src/json_schemas` is NOT retired — it is
  still the legitimate EXTRACTION-TIME validator for LLM responses (`events.py` EVENT_SCHEMA, the
  `*_ITEM_SCHEMA` per-item checks) + the type-stub/doc generators; that is a distinct role from
  record-shape validation and is out of scope for this read-vs-write reconciliation. (The write-path
  duplicate in `openserp_enrichment._validate_before_write` was already removed — it delegates to the
  central `_validate_entity`.)
