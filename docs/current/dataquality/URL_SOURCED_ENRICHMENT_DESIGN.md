# URL-Sourced Enrichment + URL Registry (design)

**Date:** 2026-10-13 · **Status:** DESIGN (not yet implemented) · **Priority:** HIGH

---

## Problem

Phase 3 structured enrichment for **people_groups** (step 1/6,
`src/extraction/enrich_groups.py`) — and the same pattern for **places** (step 2/6,
`enrich_places.py::_enrich_place_data` via `_try_grok`) — currently accepts **Grok's
GENERATED facts directly**:

- The prompt asks Grok to "look up this WWII unit" and return facts (full_name,
  unit_type, nationality, commanding_officers, notable_operations, dates,
  description).
- There is **no source URL / citation** in the schema and **no verification** — only
  the prompt's "only include facts you are confident about."
- `_apply_group_enrichment` writes the model output straight into `enrichment_data`
  and promotes it into spec fields (diff-guarded, but unverified).

This is ungrounded model output written as fact, with no citation to check —
unacceptable for the project's professional-historian provenance bar
(`verbatim_reference`, `archive_reference_number`). It also contrasts with OpenSERP
and bibliography resolution, which store a **source URL** and have Grok **verify** it.

## Trust model (agreed)

- **Grok-GENERATED facts are NOT trusted** and must not be written as fact on their own.
- **Grok is trusted to EVALUATE** — i.e. to judge a URL's relevance (right unit, WWII
  era). Grok is the evaluator/verifier, not the source of truth.
- **URL-sourced data (Grok-evaluated as relevant) has SOME trust** and is written with
  provenance.

## Compute architecture (LOCKED — owner 2026-10-13)

A SINGLE central **Lambda**, invoked with a URL as an argument (direct Invoke,
request/response) by BOTH Phase 2 and Phase 3 enrichers. The Lambda is stateless and
short; ALL coordination + the go/no-go decision live in DynamoDB (`dev-wwii-api-cache`).
NO SQS, NO dedicated container, NO in-function sleeping (so the 15-min cap / idle-billing
objections do not apply — each invocation acts or returns immediately).

Flow per call:  caller submits URL -> Lambda queries DynamoDB -> DynamoDB state yields
the ACTION (or no action) -> Lambda acts or returns.

The GO/NO-GO decision is STATE-BASED, keyed on TWO stored timestamps:
  1. URL REGISTRY entry `url#{sha256(clean_url)}`: `in_progress` / `searched`(<90d) /
     `dead` / resolved -> if already known -> NO-GO (return the stored verdict; do not
     re-fetch).
  2. PER-DOMAIN last-query time `fetchgate#{domain}.last_query_at` (owner 2026-10-13 —
     stored in DynamoDB, part of the go/no-go): if `now - last_query_at <
     politeness_interval` -> the domain was queried too recently -> NO-GO (defer).
GO only when BOTH say go (URL eligible AND domain cooldown elapsed): the Lambda stamps
`fetchgate#{domain}.last_query_at = now` (reserving the next polite slot so parallel
Phase 2/3 invocations naturally space out — whoever stamped last forces the others to
NO-GO until the interval passes), claims the URL `in_progress`, fetches (live -> Wayback
per #3/#9, SSRF-safe per-hop redirects per #5), renders the Grok content verdict, and
finalizes. This expresses per-domain politeness as a STORED-TIMESTAMP DECISION, not a
sleep or a queue (supersedes the earlier shared-library / SQS-worker / per-domain-lock
framings). `url_hygiene` + `url_registry` are the libraries this Lambda uses.

## Flow

1. **Grok must CITE source URLs.** Rework the people_groups (then places) enrichment
   prompt so Grok returns, for each fact/claim, the **source URL(s)** it is drawing
   from. Facts with no cited URL are NOT written as trusted facts.
2. **Queue the URLs.** Each cited URL goes into a URL queue for individual examination
   (not trusted just because Grok named it — Grok-claimed URLs can be hallucinated;
   the 2026-06-05 bibliography reset found ~48% hallucinated URLs).
3. **Grok renders a CONTENT verdict on each URL** (owner 2026-10-13) — on the ACTUAL
   fetched content (live or Wayback), Grok judges: relevance to THIS entity (correct
   unit/place, not a same-named unit from another war; the right "Paris") AND to WWII,
   AND detects FALSE LEADS — a page that is reachable (200, not a block page) but is the
   WRONG entity / spam / AI-slop / does not substantiate the claim. Fail-closed: reject
   on weak/no relevance or false-lead.
   - A FALSE LEAD is a CONTENT rejection, NOT mechanical `dead` (the page loads fine) —
     recorded with Grok's REASON ("false lead: wrong Paris"), scoped per (URL, entity)
     via the #4 hybrid (optionally a GLOBAL reject for pure spam/slop), and RE-RENDERABLE
     on recheck (re-running Grok on the content IS the re-verification path — the #9
     reversibility). This is distinct from reachability (link rot / dead), which the
     mechanical fetch path + #9 persistence rule handle.
   - "validate relevance" operates on the fetched CONTENT (fetch -> Grok judges), not the
     URL string/snippet — because Grok-claimed URLs are often fabricated (48%-bad-URL
     2026-06-05 reset). [resolved: fetch-then-evaluate, open decision #1]
     string/snippet — because Grok-claimed URLs are often fabricated. Default assumption
     here = fetch-then-evaluate. Confirm.
3b. **Link rot — ONE universal fetch behavior (owner 2026-10-13).** Link rot is
   pervasive, so this is NOT a step-3-only concern: there is a SINGLE centralized URL
   fetch that EVERY path GETting a URL routes through (enrichment, bibliography
   resolution, award sources, map imports, OpenSERP result fetches — pipeline-wide),
   with the same rule:
   - Fetch live. If it 404s / errors / returns an HTTP-200 BOT-BLOCK or HUMAN-TEST
     page — WAF challenge / CAPTCHA ("verifying you are human", Cloudflare
     "enable JavaScript and cookies", PerimeterX px-captcha, Incapsula, Akamai, and
     (markers to ADD) reCAPTCHA/hCaptcha, Google "/sorry/ unusual traffic") detected by
     `wayback.is_block_page` — FALL BACK to the Archive.org Wayback snapshot
     (`wayback.find_snapshot` / `fetch_archived`) and record provenance
     (`source: "wayback"`, `archived_url`, `wayback_capture_timestamp`).
   - HUMAN-TEST / CAPTCHA policy (owner 2026-10-13): we do NOT attempt to SOLVE or
     defeat the challenge. If we can't BYPASS it, we try Archive.org — a Wayback
     capture predates the challenge wall, so the archived snapshot is the bypass path.
     (TODO: extend `is_block_page` markers with `recaptcha`, `g-recaptcha`, `hcaptcha`,
     `www.google.com/recaptcha`, "our systems have detected unusual traffic", "/sorry/"
     so interactive CAPTCHAs are caught, not just WAF walls.)
   - If Archive.org ALSO has no record (no snapshot) -> DEAD EVERYWHERE: mark BOTH the
     original URL AND the archive.org attempt as **"do not search again"** in the URL
     registry (terminal verdict `status: "dead"`) so no future fetch ever retries a URL
     that is gone/blocked on the live web and absent from the archive.
   - REUSE the existing `src/enrichment/wayback.py` (`find_snapshot`, `fetch_archived`,
     `is_block_page` + per-host politeness) — already used by `enrich_biographies.py`
     but ONLY there. CENTRALIZE it so EVERY URL fetch gets the same handling, not just
     biographies. Grok evaluates the ARCHIVED content when the live page is dead/walled.
4. **URL REGISTRY (90-day TTL).** Persist every examined URL + verdict + timestamp.
   Subsequent Grok OR OpenSERP searches consult the registry: within **90 days** a
   known URL is REUSED (not re-fetched/re-examined); after 90 days it is eligible for
   re-examination. (TTL = 90 days, owner-confirmed 2026-10-13 — NOT the 180-day
   `enrichment.re_search_after_days`; aligns with the existing 90-day
   `openserp_searched` gate.)

## Reuse existing infrastructure (do NOT duplicate)

- `src/enrichment/enrichment_gate.py::should_re_search` — the date-stamped staleness
  gate (reads `enrichment.re_search_after_days`, currently **180**; its docstring
  wrongly says "default 90" — fix the docstring). This is the ENTITY re-search window,
  distinct from the new URL-registry TTL.
- `openserp_enrichment.py` 90-day `openserp_searched` / `openserp_searched_at` gate —
  the model for a 90-day search window.
- `openserp_enrichment.py::_URL_FETCH_RETENTION_DAYS` + the url-fetch cache backend —
  the closest existing "URL cache"; the new URL REGISTRY generalizes this to a
  cross-source (Grok + OpenSERP) visited-URL store with verdicts.
- Grok verification pattern (`_verify_result` / `_verify_nara_match`) — reuse as the
  URL-relevance evaluator (cached, fail-closed).

## Relationship to Source Qualification (ratings) — one system, not two

This work CAPTURES + relevance-checks URLs; the EXISTING
`SOURCE_QUALIFICATION_DESIGN.md` RATES them. They compose — do not invent a parallel
rating here. Every URL this flow accepts becomes (or links to) a bibliography source
carrying the two-axis `source_qualification` (source_type + evidentiary_support),
derived by that doc's deterministic ladder, crosswalked to Dublin Core on export:

```
URL / source                                     source_qualification        rating
-----------------------------------------------  --------------------------  ------
NARA / archival reference                        primary / contemporaneous   HIGH
Green Book etc. (densely footnoted secondary)    secondary / dense           MED-HIGH
book w/ bibliography, few footnotes              secondary / light           MED
bare web page asserting facts, cites nothing     secondary|tertiary / none   LOW
Grok fact with NO cited URL at all               unknown (untrusted, flagged) LOWEST
```

So the trust tiers here are the SAME signal as the qualification rating: a cited URL
Grok-evaluated as relevant gets a real source_type/evidentiary_support rating; a
Grok fact with no URL is `unknown` + flagged (never written as trusted fact). The URL
registry stores the examined URL + relevance verdict; the qualifier (authority rating)
is derived + stored on the bibliography record per SOURCE_QUALIFICATION_DESIGN.md
(Option B: compact local qualifier now, dcterms:* crosswalk at export).

---

## New component: URL registry (DynamoDB — owner-specified)  [IMPLEMENTED 2026-10-13]

Two modules landed (tracker + clean-URL gate); the enrichment WIRING (prompts that
require cited URLs, the Grok relevance evaluation, calling into these) is the
remaining build.

### Clean-URL gate — `src/utils/url_hygiene.py`
Every URL is validated + normalized by `clean_url(url)` BEFORE it is fetched,
Grok-evaluated, or stored (a URL becomes a fetch target + a DynamoDB key + record
provenance, so it must be clean first). Rules:
- Scheme must be http/https; reject `javascript:`/`data:`/`file:`/`ftp:` etc.
- SSRF-safe: reject localhost/loopback, the cloud metadata IP (169.254.169.254),
  and any private/link-local/reserved/multicast IP literal (incl. IPv6 `[::1]`).
- Reject embedded credentials (`user:pass@`) and over-long (>2048) / malformed URLs.
- NORMALIZE for stable dedup: lowercase scheme+host, drop default ports (80/443),
  drop the fragment, and **drop the query EXCEPT a resource-identity allowlist**
  (`id, q, query, search, page, p, pg, docid, doc, recordid, naid, arcid,
  identifier, title, oldid, v`) — OWNER DECISION (option 2): everything after `?` is
  ignored EXCEPT identity params, so tracking/session noise collapses but distinct
  documents (NARA `?id=`/`?q=`, viewer `?page=`) stay distinct. Kept params sorted.
- Returns the canonical URL, or None if not clean (fail-closed).

### Tracker — `src/utils/url_registry.py`
- Store: DynamoDB `dev-wwii-api-cache`; key `url#{sha256(clean_url)}`; value JSON
  {url, relevant, context, source, examined_at, status}; `status` in
  {searched, dead}.
- TWO retention behaviors by status (owner 2026-10-13):
## Concurrency — the registry MUST use a conditional claim (not get-then-put)

Multi-doc mode runs per-book phase tasks IN PARALLEL (confirmed:
`trigger_handler._lock_key` is per-book `lock#{family}#{book}` "so different books hold
the same phase concurrently"; the dispatcher claims a backlog in parallel). Different
WWII books cite the SAME common URLs (Hodges, First U.S. Army, shared Green Books), so
two parallel tasks hitting the same URL both run `should_examine` (GetItem) -> both see
"not registered" -> BOTH fetch + BOTH Grok-evaluate — the exact repeat the registry
exists to prevent. The registry dedups across TIME but a naive get-then-put does NOT
dedup across CONCURRENT tasks.

FIX (owner 2026-10-13 — `in_progress` claim/finalize/reclaim; same discipline already
proven here via `_title_claim`, the `_run_task` lock, SNS-subset conditional rewrites):
the registry has THREE states — `in_progress` (claimed, being worked now), `searched`
(done, 90-day TTL), `dead` (done, permanent). Flow:
  1. CLAIM atomically before fetching: `put_item(status="in_progress", claimed_at=now,
     ConditionExpression="attribute_not_exists(cache_key)")`.
  2. WINNER (claim succeeds) -> fetch + Grok-eval + Wayback -> FINALIZE: update to
     `searched` (verdict + 90-day ttl) or `dead` (no ttl).
  3. LOSER (claim fails, key exists) -> sees `in_progress`/`searched`/`dead` -> DEFERS,
     does NOT fetch. Converts "both fetch" into "one fetches, the others defer".
  STALE-CLAIM GUARD (required, else `in_progress` becomes a worse bug): if the winner
  CRASHES (spot kill / OOM / timeout) after claiming but before finalizing, a bare
  `in_progress` would strand the URL forever (every future task skips "it's being
  worked" — but no one is, exactly the Phase-1 stale-lock bug we hit). So `in_progress`
  carries `claimed_at` + a short staleness WINDOW: an `in_progress` within the window ->
  defer (a live task owns it); OLDER than the window -> presumed-dead claim -> RECLAIM
  and proceed. (Same `_TITLE_CLAIM_WAVE_S` / `LAUNCH_GRACE_S` reasoning, reused.)
  NOTE: the already-built `url_registry.register` is an unconditional put with only
  searched -> extend it to the claim(in_progress)/finalize(searched|dead)/reclaim model.

## OPEN RISKS / logic holes to resolve BEFORE build (2026-10-13)

1. CONCURRENCY (above) — conditional-claim register. [resolution known]
2. REGISTER-BEFORE-WRITE data loss: a URL marked "searched" (skip 90 days) whose result
   never landed in the entity JSON (crash / S3 sync fail between register and write) =
   silent 90-day data loss. -> RESOLVED by the `in_progress` claim/finalize model above:
   a crash mid-work leaves `in_progress` (reclaimable after the staleness window), NOT a
   false `searched`. Finalize to `searched` only AFTER the result write confirms, so
   "searched" always implies "data landed".
3. TRANSIENT-OUTAGE -> PERMANENT `dead`: RESOLVED (owner 2026-10-13 — "ignore transient
   outages, but again fail out to archive.org"). `dead` (permanent) requires TWO
   AUTHORITATIVE negatives: live says confirmed-GONE (404/410) AND Wayback
   authoritatively says NO snapshot. On ANY live failure — transient
   (timeout/5xx/DNS/egress blip/Wayback-call-errored) OR CAPTCHA-walled — still FAIL OUT
   to Archive.org (Wayback may have it and rescue the run now). If the live failure was
   TRANSIENT (not an authoritative 404/410) OR the Wayback call itself errored (couldn't
   confirm absence) -> IGNORE: record NO verdict, so the URL is simply retried on a
   future run. A network blip can therefore NEVER mark a good URL permanently `dead`.
4. PER-URL RELEVANCE vs PER-ENTITY context: relevance is relative to the ENTITY being
   enriched, not an absolute property of the URL — a single `relevant` verdict reused
   across entities is a correctness bug (en.wikipedia.org/.../First_United_States_Army
   is relevant to the unit but not necessarily to a person/place that also cites it).
   RESOLVED (owner 2026-10-13) = HYBRID: split the two concerns —
     * FETCH STATE (searched/dead/in_progress) is URL-keyed (`url#{sha(clean_url)}`) and
       cached ONCE EVER (page content is the same regardless of who asked — the dedup
       win: fetch once).
     * RELEVANCE VERDICT is keyed per (URL, entity) (e.g. `rel#{sha(url)}#{entity_id}`)
       — a URL is relevance-judged ONCE PER ENTITY that references it, reused only within
       that same (URL,entity) pair on re-runs; NEVER reuse entity A's verdict for entity
       B. Net: fetched once; relevance-judged once per referencing entity.
   ALTERNATIVES (recorded for later review if hybrid proves non-viable — e.g. (URL,entity)
   key cardinality / storage cost on a large corpus):
     * Option A — fetch-state URL-keyed only; relevance re-judged per entity EVERY run
       against the already-fetched content (no relevance cache). Simplest, lowest
       storage; re-runs re-pay the Grok relevance eval.
     * Option B — relevance keyed on (URL,entity) with its own cache (what the hybrid
       uses for the relevance half). Caches the eval; more rows.
     * Option C — reuse a verdict only within the same entity TYPE or same entity
       (weaker scoping). Cheapest-but-least-correct; last resort.
5. REDIRECTS: the cited short/redirecting URL (bit.ly, doi.org, http->https, archive
   migrations, wikipedia ?curid=) and the final resolved URL are different keys -> same
   destination searched twice; a verdict on one form doesn't apply to the other.
   RESOLVED (owner 2026-10-13): dedup identity = the FINAL / RESOLVED (post-redirect)
   clean URL. Claim `in_progress` on the cited clean URL (all we have pre-fetch); fetch
   FOLLOWING redirects; finalize keyed on the final clean URL and ALIAS the cited URL to
   it (so a future task citing EITHER form dedups). Result-dedup identity in the entity
   JSON = the final/canonical URL; the originally-cited URL is kept as provenance for
   traceability.
   MANDATORY per-hop SSRF re-check (security — this hole REOPENS the SSRF vector
   clean_url closes at the front door): redirects let a clean-looking public cited URL
   302 -> an internal/metadata target (e.g. http://169.254.169.254/ IAM creds). So do
   NOT `requests.get(allow_redirects=True)` blindly — follow redirects MANUALLY one hop
   at a time, run `url_hygiene.clean_url` (SSRF/scheme/host check) on EACH `Location`
   BEFORE following it, and ABORT if any hop targets a private/loopback/link-local/
   metadata host or a non-http scheme. (url_hygiene currently validates only the input
   URL — extend the fetch to re-validate every hop.) Cap hops (e.g. 5); a redirect loop
   / excess hops -> failure (transient per #3, i.e. ignore + try Wayback).
6. MERGE-ON-RECHECK DOWNGRADE: upsert must NOT overwrite good data with a worse/empty
   re-search result (or a slipped-through block page) — merge-only-absent / don't-
   downgrade rule.
7. `checked_at` vs CHANGE-DETECTION: RESOLVED (owner 2026-10-13). Principle: "we looked
   again" is NON-CONTENT metadata; "what we found changed" is content. Recheck-tracking
   fields (`checked_at`, `history[]`, liveness `source`/`archived_url`, and the existing
   `*_checked_at`/`last_enrichment_search` markers) MUST be excluded from the content
   hash so a pure recheck (same facts, new timestamp) yields the SAME hash -> NO
   re-publish/re-embed; only a substantive content change re-publishes. Mechanism exists:
   extend `validation_stats._NONCONTENT_FIELDS` (already excludes _schema_version/
   _last_updated/enrichment_status/last_enrichment_search) and define the future loader's
   content hash to hash content fields only. NOTE: the change-detector is a FUTURE phase
   (loader TODO) so #7 is currently LATENT — but the convention must be set WHEN the #6
   fields are added, or we build the re-embed hole.
   SCHEMA CHANGE — DEFERRED (owner decision 'b', 2026-10-13): the multi-schema change
   that #6/#7 require (add result-entry `history[]`/`claim_type`/`confirmed`/`checked_at`/
   `source`/`archived_url` to places/people/people_groups/bibliography/source_section/
   equipment result-list fields + add them to _NONCONTENT_FIELDS + bump each entity
   version + re-pin fingerprints) is HELD until the design is fully settled (#8-#10 +
   the final history/claim_type shapes), so versions + fingerprints are cut ONCE against
   the final model, not re-cut as the design evolves. Do NOT edit schemas yet.
8. WAYBACK/host RATE-LIMIT / politeness: RESOLVED (owner 2026-10-13 — "behave politely;
   parallel BY DOMAIN only; the same domain handled politely and SEQUENTIALLY").
   Concurrency model for ALL fetches: parallel ACROSS domains (Wikipedia / Nominatim /
   Archive.org progress at once), but SERIALIZED within a single domain — only ONE
   in-flight request to a given domain at a time, spaced by a politeness interval. This
   satisfies Nominatim's strict 1/sec regardless of how many parallel book tasks run,
   while keeping throughput high (different domains don't block each other). Subsumes a
   simple per-task throttle: the gate must GLOBALLY serialize per domain across ALL
   parallel tasks.
   CROSS-TASK enforcement: book tasks are SEPARATE ECS containers, so "sequential per
   domain" cannot be in-process only — it needs a SHARED DynamoDB per-domain gate
   (per-domain last-call timestamp / short lock in `dev-wwii-api-cache`, e.g.
   `fetchgate#{domain}`) that every fetch acquires + respects before hitting that domain.
   Applied UNIFORMLY to every domain (not tiered by host) = the owner's "same domain ->
   sequential" rule.
   Plus standard courtesy: honest User-Agent (project + contact, per Nominatim/Archive.org
   policy — NOT a spoofed browser), honor `Retry-After`/429/503 with backoff, per-run
   fetch budget + circuit breaker. REUSE `wayback.py`'s `_throttle(host)` / circuit
   (`reset_circuit`) and the bibliography_resolver `Retry-After` handling — generalize
   into the one centralized fetcher.
9. NO INVALIDATION / verdict reversibility: RESOLVED (owner 2026-10-13). `dead` is
   PERSISTENCE-based, not single-failure:
   - `dead` (permanent) requires >= 4 DNS/unreachable failures at 90-day increments
     (~1 year of consecutive quarterly rechecks failing) AND no Archive.org snapshot
     ever found. A single/transient failure NEVER qualifies (ties to #3: transients are
     ignored); any success in between resets. Needs a failure count + dates — the #6
     `history[]` provides exactly this.
   - ARCHIVE.ORG "permanently resolved" -> ABORT further Wayback search: if the latest
     Wayback capture timestamp is UNCHANGED since the first time we recorded it (the page
     is no longer being re-captured), the archived content is FINAL — STOP re-querying
     Wayback for a newer capture (don't poll for a snapshot that will never update). This
     is a terminal "don't re-fetch, we have the final archived version" state for that
     URL (distinct from `dead` = gone; this = resolved).
   - This gives the reversibility #9 asked for WITHOUT a manual appeal: a URL only goes
     `dead` after sustained (~1yr) failure, and Grok re-renders CONTENT verdicts on each
     recheck (incl. FALSE-LEAD detection — a reachable 200 page that is the wrong entity/
     spam/doesn't substantiate; recorded with Grok's reason, re-renderable, NOT `dead`).
   OPEN sub-points for build: (i) does a clean 404/410 (authoritatively gone) resolve to
   `dead` FASTER than the 4x90-day DNS persistence rule, or follow the same patience?
   (ii) "permanently resolved" is a terminal don't-re-fetch state — stored archived
   content is used as-is thereafter.
10. GROK FACT WITH NO CITED URL: RESOLVED (owner 2026-10-13 — "if Grok asserts a fact
    without a URL, ignore the response until it can provide a URL"). DROP, not flag: an
    unsourced Grok assertion is IGNORED entirely — not written, not stored, not parked
    as `grok_unverified`. No URL => no record. (This closes open decision #3 in favor of
    DROP; the "flag `grok_unverified`" option is rejected.) NOT a permanent ban on the
    claim: it is simply not recorded THIS time; if a later run / reworked prompt gets
    Grok to cite a URL for that claim, it flows through the normal pipeline (fetch ->
    content verdict -> confirmed-fact|opinion -> write). Consequence: EVERY recorded
    claim has a source URL, so nothing ungrounded ever enters the corpus — and there is
    no "unsourced fact" tier for claim_type to handle (every stored claim is a
    URL-sourced confirmed fact or a URL-sourced tagged opinion).
    attempt are recorded `dead`.)
  - NOTE: the already-built url_registry currently TTLs EVERYTHING at 90 days and has no
    `status`/`dead` path — extend it: add `status`, omit/elide the `ttl` attribute for
    `dead` (DynamoDB native TTL only deletes items that HAVE a future ttl; a dead item
    carries none so it persists), and make `should_examine`/`lookup` honor `dead`.
- `register(url, relevant, context, source)` — clean-URL gate FIRST (non-clean ->
  rejected, returns None, nothing stored); else writes with 90-day TTL. Best-effort,
  never crashes enrichment.
- `lookup(url)` — normalizes, returns the non-expired UrlRecord or None (expired ->
  None = eligible for re-examination).
- `should_examine(url)` — True if clean AND not examined within 90 days.
- Local/test (aws.enabled false): the clean-URL gate still runs; persistence is a no-op.
- Consulted by BOTH the Grok-cited-URL examination and OpenSERP result handling so the
  two share ONE visited-URL memory and never re-hit the same URL within 90 days.

### What the registry stores — "SEARCHED this URL", NOT the result (owner 2026-10-13)
- The registry records that we SEARCHED a clean URL (verdict + context + timestamp).
  It does NOT store the result payload — the actual results already live in the
  feature JSON (the place/group/person entity file: coordinates, images, etc.). So a
  registry HIT means "we already searched this URL -> SKIP re-searching"; the data is
  already in the entity JSON from the first search. (Do not generalize this into a
  result/response cache — explicitly rejected.)
- After the 90-day TTL expires, if the place/entity surfaces again it is RE-SEARCHED
  (refresh). `should_examine` returns True once the entry is gone/expired.
- This is the SAME prevent-repeat concern as geocoding re-hitting Nominatim for
  "Paris" on every Phase 3 run (the geocode on-disk cache is ephemeral + not synced,
  so it repeats across runs). Routing endpoint searches through the shared registry
  (keyed on the clean request URL) makes "search this URL once per 90 days" uniform
  across Grok/Wikipedia/OpenSERP/geocode and across Phase 2 + Phase 3.

### OUT OF SCOPE (different function): preventing DUPLICATE RESULTS in the JSON
- Not re-SEARCHING a URL (this registry) is distinct from not WRITING a duplicate
  result into the feature JSON. Result/entity dedup within the feature file is a
  SEPARATE function (owner 2026-10-13) and is NOT part of the URL registry.

## Scope / sequencing

- people_groups first (this request); places next (identical `_try_grok` trust gap).
- Depends on nothing else, but complements the bibliography citation-text prerequisite
  (TODO HIGH #2) and the resolve-then-download work (TODO HIGH #3).

## Centralized across BOTH phases (owner directive 2026-10-13)

The URL registry is SHARED, phase-agnostic code (`src/utils/url_registry.py` +
`src/utils/url_hygiene.py`) used by **Phase 2 AND Phase 3** — NOT a Phase-3-only
feature and NOT reimplemented per phase. Goal: prevent REPEATED lookups across the
whole pipeline. Both of these consult the same registry (one visited-URL memory,
90-day TTL):
  - **Phase 2** Grokipedia/Wikipedia resolution (grokipedia.py, *_wikipedia.py,
    enrich_biographies/enrich_places/people_groups extractors): before resolving a
    name->page URL and fetching it, check the registry; after, register the
    resolved URL + relevance verdict. A Grokipedia/Wikipedia page examined by EITHER
    phase is not re-fetched within 90 days.
  - **Phase 3** OpenSERP results + Grok-cited URLs: same register/lookup.

RELATION TO THE EXISTING `search_cache` (do NOT confuse/duplicate):
  - `search_cache` keys on the QUERY STRING (`search#{source}#{query}`) — prevents
    re-running the same SEARCH. It is query-keyed.
  - the URL registry keys on the RESOLVED DESTINATION URL (`url#{sha256(clean_url)}`)
    + carries a relevance VERDICT — prevents re-FETCHING/re-EVALUATING the same page.
  These are COMPLEMENTARY layers, both centralized; keep both, don't merge.
  The scattered per-entity date markers (`grokipedia_checked_at`, `wikipedia_checked`,
  `openserp_searched`, `last_enrichment_search`) are a SEPARATE "did I enrich this
  ENTITY" concern — consolidating those is a later, bigger refactor (open decision 4),
  NOT part of this URL-registry centralization.

## Open decisions

1. [RESOLVED 2026-10-13 — fetch-then-evaluate] The URL-relevance step FETCHES the page
   and Grok evaluates the actual CONTENT (not just the URL string/snippet), because
   Grok-claimed URLs are frequently hallucinated (48%-bad-URL history). Register the
   verdict either way so a dead/irrelevant URL isn't re-fetched for 90 days.
2. [RESOLVED 2026-10-13 — together] people_groups AND places in the SAME change (both
   have the identical `_try_grok` source-less gap; both use the shared registry +
   clean-URL + fetch-then-evaluate code). Keep `--max-items` usable for the first full
   run since places is higher-volume (fetch-per-URL cost bounded by the 90-day
   registry + should_examine gate).
3. [RESOLVED 2026-10-13 — DROP] Grok facts with NO cited URL are IGNORED entirely
   (not written, not flagged) until Grok provides a URL; re-openable on a later run.
   See OPEN RISKS #10. (The "flag `grok_unverified`" option is rejected.)
4. [DEFERRED — recommend leave] consolidating the scattered per-entity date markers
   (`grokipedia_checked_at`, `wikipedia_checked`, `openserp_searched`,
   `last_enrichment_search`) is a separate, bigger refactor; the URL-registry
   centralization stands alone.
