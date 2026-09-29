# Ingestion Front Door — Current State (OCR is one branch)

_Branch: `feature/concurrency-parallelism` · Last updated: 2026-09-29_

Status of the unattended, document-parallel WWII pipeline. **OCR (scanned PDFs /
images) is only one intake branch** — the front door also takes HTML, EPUB,
DOCX, TXT, and Markdown, each with a different conversion route into the common
parse → extract → enrich chain.

---

## Intake is multi-format (OCR is a subset)

Source formats and their intended route to Markdown (the common substrate the
parse/extract chain consumes):

| Format | Media type | Track | Route to Markdown |
|--------|-----------|-------|-------------------|
| Scanned PDF | `pdf` | narrative | **OCR** (Chandra GPU Batch) |
| Digital PDF | `pdf` | narrative | text extract / OCR fallback (`pdf_pipeline`) |
| Image (jpg/png/tif/…) | `image` | vision | **OCR** (Chandra) + caption |
| DOCX | `docx` | narrative | **pandoc** (`docx_to_markdown`) |
| EPUB | `epub` | narrative | **pandoc** (`epub_to_markdown`) |
| HTML | `html` | narrative | HTML→md (`web_video` / pandoc) |
| TXT | `text` | narrative | `txt_to_markdown` |
| Markdown | `text` | narrative | passthrough (already md) |
| Video | `moving_image` | av | transcript (`web_video` + transcriber) |
| Archive (.zip/.rar) | — | — | expanded by pre-stage; never processed directly |
| unsupported | `unsupported` | skip | → `needs-review` |

The conversion capability already exists in `src/ingestion/`:
- `media_detection.detect_media_type` — content-type + extension + magic-byte sniff
- `text_converters.convert_to_markdown` → `docx/epub/txt_to_markdown` (pandoc)
- `pdf_pipeline.convert_pdf_to_markdown` + `_is_scanned_pdf` (digital vs scanned)
- `web_video` — HTML/video capture + transcription
- `prestage.route_file` → `_ROUTING` table (the intended per-format router)

### Gap to close (routing)

`prestage._ROUTING` is the **intended** per-media-type router, but the deployed
Lambda `trigger_handler._split_by_media` currently does only a coarse split:

```python
# trigger_handler._split_by_media (current)
pdfs   = [k for k in keys if k.endswith(".pdf")]   # -> OCR
others = [everything else]                          # -> parse (assumes markdown!)
```

So **images, EPUB, HTML, DOCX are sent straight to the markdown parse path**
instead of through their conversion step. PDFs correctly go to OCR; the rest
should be split into: `convert` (docx/epub/html/txt → pandoc), `ocr`
(image), and `passthrough` (md) — mirroring `prestage._ROUTING`. The converters
exist; the Lambda router just needs to call them (or defer to Phase 0
`route_file`). Tracked as a follow-up.

---

## Where the OCR branch is (verified this session)

The OCR RUNNABLE-stall and the follow-on container-pull failure are **both
fixed and deployed**. A live on-demand OCR job (`B406`, 11 pages):

1. Passed the **readiness gate** (NAT + all 7 VPC interface endpoints
   `available`) before compute was requested.
2. **Registered** its GPU instance with the ECS/Batch cluster (the thing that
   failed every prior attempt).
3. **Pulled the Chandra image** via the ECR endpoints (past the point that
   failed with `CannotPullECRContainerError`).
4. Ran OCR to `SUCCEEDED` with NAT + endpoints held stable throughout (the
   teardown guard, once given `batch:ListJobs`, kept infra up).
5. …but produced **no output** — Chandra hit CUDA OOM on page 1 (16GB g4dn) and
   silently exited 0. This surfaced the GPU-memory handling gap, now fixed
   (below).

### GPU CUDA-OOM handling (layered: anticipate + fail-and-escalate)

The infra chain is proven end-to-end; OOM was the remaining application-layer
gap. Fixed in three layers (commit `84a2a00`):

1. **Fail-loud** — `ocr_watchdog` detects the CUDA-OOM line in Chandra's stream
   and returns `EXIT_OOM (76)` even when Chandra exits 0, so the job fails
   visibly instead of "succeeding" with empty output.
2. **Anticipate-first** — `trigger._pdf_has_large_page` probes page mediabox
   area at submit; oversized pages (the empirical OOM trigger on 16GB) route
   straight to the 24GB high-VRAM queue, skipping a wasted attempt.
3. **Fail-and-escalate** — `ocr_spot_controller._escalate_oom_failures`
   resubmits an OOM-failed job (exit 76) **once** to the high-VRAM queue
   (24GB g5/g6, g4dn excluded); a second OOM there is left FAILED for review.
   This "upsizes + recycles" automatically: Batch scales the failed 16GB
   instance down and launches a fresh 24GB one.

Deployed + verified: `wwii-ocr-dev` UPDATE_COMPLETE with the new
`dev-wwii-chandra-gpu-highvram` queue + `-highvram-v1` CE (ENABLED/VALID);
controller runs `{routed:0, escalated:0, capped:0}` cleanly (escalation path +
IAM confirmed).

### Two root causes fixed this session

| # | Symptom | Root cause | Fix (commit) |
|---|---------|-----------|--------------|
| 1 | GPU job stuck RUNNABLE; instance launches but never registers | NAT + interface endpoints not reliably up at boot; nat_manager **create-after-delete race** (a `deleting` endpoint counted as "present" → recreation skipped) | Readiness contract `_verify_ready` + exclude `deleting` from the exists-check + `_wait_out_deleting` (`4bb5ee0`) |
| 2 | Instance registers, job RUNNING, then `CannotPullECRContainerError` (ECR auth timeout) | A **direct `action=delete`** tore down NAT+endpoints **under a running job** — the demand guard only ran on the SNS path | Move demand guard **into `_delete_all`** (every delete path honors in-flight OCR); `force=True` for intentional teardown (`561e71f`) |

### Not a bug (clarified)

- **Spot not placing immediately is expected.** Spot waits for capacity; the
  Option-B controller falls back to on-demand after the threshold (48h cap).
  On-demand places immediately (768 quota). Only the spot *quota* matters, and
  the ≥80% warning already surfaces it.

---

## Flow + decision points

```mermaid
flowchart TD
    A[File uploaded to S3 raw] --> B[trigger_handler: filter content suffixes<br/>drop archives/.zip - pre-stage expands]
    B --> C{OCR/dup intake claim<br/>ocr#book exists?}
    C -->|claimed already| C1[Deny duplicate submit]
    C -->|new| RT{{"ROUTE BY MEDIA TYPE<br/>detect_media_type + prestage._ROUTING<br/>(gap: Lambda _split_by_media only<br/>splits pdf vs rest today)"}}

    RT -->|scanned pdf / image| D[OCR branch]
    RT -->|docx / epub / html / txt| CV[Convert branch:<br/>pandoc convert_to_markdown /<br/>web_video for html]
    RT -->|markdown| PT[Passthrough:<br/>already markdown]
    RT -->|video| AV[AV branch:<br/>transcript]
    RT -->|unsupported| NR["needs-review (H1)<br/>👤 human inspects"]

    %% ---- OCR branch (verified this session) ----
    D --> D2[Best-guess chunking _ocr_chunks]
    D2 --> LP{{"ANTICIPATE-FIRST _pdf_has_large_page<br/>oversized page (pt² > threshold)?"}}
    LP -->|yes: large page| HV0[route to high-VRAM queue up front]
    LP -->|no| E[_ensure_nat_for_ocr:<br/>nat-manager action=create]
    HV0 --> E
    E --> F{{"READINESS GATE _verify_ready<br/>NAT + all 7 endpoints available?"}}
    F -->|not_ready| E
    F -->|ready| G[Submit Batch OCR job]
    G --> H{Queue}
    H -->|spot default| I[chandra-gpu spot<br/>waits for capacity - expected]
    H -->|on-demand| J[chandra-gpu-ondemand<br/>places immediately]
    H -->|large page / OOM escalation| HV[chandra-gpu-highvram<br/>24GB g5/g6 only, no g4dn]
    I -->|no capacity &gt; threshold| K[ocr_spot_controller<br/>-&gt; on-demand, 48h cap]
    K --> J
    I --> L[GPU instance launches]
    J --> L
    HV --> L
    L --> M{{"Registers with ECS?<br/>egress: ecr.api/dkr, ecs,<br/>ecs-agent/telemetry, logs, secrets"}}
    M -->|no egress| M1["FAIL: CannotPullECRContainerError<br/>(FIXED: readiness gate + teardown guard)"]
    M -->|endpoints present| N[STARTING: pull Chandra image via ECR endpoint]
    N --> O[RUNNING: download PDF via S3 gateway endpoint]
    O --> P[Chandra OCR pages 1..N<br/>ocr_watchdog: 900s no-progress + OOM detect]
    P --> OOM{{"watchdog: CUDA OOM detected?<br/>(exit 76, even if Chandra exits 0)"}}
    OOM -->|OOM on 16GB| OJ[Batch job FAILED exit 76]
    OJ --> ESC{{"ocr_spot_controller<br/>_escalate_oom_failures<br/>escalated once already?"}}
    ESC -->|no| HV
    ESC -->|yes 2nd OOM on 24GB| ORV["leave FAILED<br/>👤 needs-review + alert"]
    OOM -->|clean| Q[Write ocr-output/book/...]
    Q --> MR{{"👤 OCR→Markdown review (H2)<br/>mdreview_ui UI - BUILT, NOT DEPLOYED<br/>page image + editable snippet"}}
    MR -->|reviewed pN.md saved| MRS["ocr-output/book/reviewed/pN.md<br/>(consume-once at merge)"]
    MR -->|no review / auto| R[Batch job SUCCEEDED]
    MRS --> R
    R --> S[EventBridge dev-wwii-ocr-succeeded]
    S --> T["ocr_merge_handler<br/>(substitutes reviewed pages)"]
    T --> U["contentrepository/book/chapter1/<br/>chapter1-content.md + chapter1-meta.yaml (A1)"]

    %% ---- all branches converge on markdown ----
    CV --> U
    PT --> U
    AV --> U
    U --> HR{{"👤 weak structure /<br/>OOB suspect cells?<br/>(H3/H4 review off-ramp)"}}
    HR -->|flagged needs_review| RVW["needs-review queue<br/>👤 confirm/correct"]
    HR -->|clean| V[content-upload event triggers parse]
    V --> W[Phase 1 parse to JSON]
    W --> X[Phase 2 extract entities Grok Batch]
    X --> XD[duplicate_report.json<br/>people/places/groups/equipment]
    XD --> DG{{"👤 DEDUP REVIEW GATE (H5)<br/>dedup_ui_handler /dedup UI<br/>merge / skip / exclude"}}
    DG -->|doc blocked| OFR["_offramp_doc_needs_review (H6)<br/>doc -&gt; needs-review"]
    DG -->|POST /complete<br/>ungates Phase 3| Y[Dedup applied]
    Y --> Z[Phase 3 enrich]
    Z --> ZZ[done]

    %% ---- teardown decision ----
    R --> TD{{"nat-manager delete<br/>_nat_demand_present?"}}
    TD -->|OCR jobs in flight| TD1[REFUSE teardown - keep infra up]
    TD -->|idle & not force| TD2[Tear down NAT + endpoints]
    TD -->|force=true operator| TD2

    %% 👤 = human intervention (H1-H6). H2 & H5 are indefinite async gates: NAT DOWN while waiting.
    %% Operator alerts (H7) fire throughout via email+Slack.
```

### Key decision points (numbered)

0. **Route by media type** — `detect_media_type` (content-type + extension +
   magic bytes) → `prestage._ROUTING` picks the branch: OCR (scanned pdf /
   image), convert (docx/epub/html/txt → pandoc), passthrough (markdown), av
   (video), or skip (unsupported → needs-review). **All branches converge on
   Markdown**, then the common parse → extract → enrich chain. *(Gap: the
   deployed Lambda router only splits pdf-vs-rest today — see above.)*
1. **OCR intake claim** — `ocr#{book}` DynamoDB claim guards the whole chunk
   set; duplicate submits denied at the front door (dedup principle).
2. **Chunking** — page-count > 50 → 50-page chunks; image → single; unknown →
   safe whole fallback (`_ocr_chunks`).
3. **Readiness gate** (`_verify_ready`) — compute is **not requested** until
   NAT is `available` AND every required interface endpoint is `available`.
   Returns `{ready, missing[]}`. This is what makes registration reliable.
4. **Queue selection** — three queues: **spot** (default; waits for capacity,
   expected), **on-demand** (controller fallback after threshold, 48h cap), and
   **high-VRAM** (24GB g5/g6; anticipate-first for large pages + OOM-escalation
   target). `ocr_spot_controller` owns the spot→on-demand and OOM→high-VRAM
   moves.
5. **GPU VRAM / OOM** — anticipate-first (`_pdf_has_large_page` at submit) routes
   oversized pages to high-VRAM up front; `ocr_watchdog` turns a swallowed CUDA
   OOM into a visible `exit 76`; the controller escalates it once to high-VRAM
   (upsize + recycle), else needs-review.
6. **Registration egress** — the instance needs the 7 interface endpoints
   (ECR/ECS/logs/secrets) to register + pull; S3 uses the gateway endpoint.
7. **Teardown guard** (`_delete_all` + `_nat_demand_present`) — refuses to tear
   down NAT/endpoints while any OCR job is non-terminal on any queue, unless
   `force=true`. Requires `batch:ListJobs` (the audit gap) + fails safe.

---

## Human intervention points (not fully automated)

The pipeline is unattended by default but has explicit human-in-the-loop gates
and review off-ramps. These are real code paths, not aspirational:

| # | Where | Trigger | Human action | Effect |
|---|-------|---------|--------------|--------|
| H1 | **Intake routing** | `unsupported` media type (`prestage._ROUTING` → skip) | inspect the source | doc set to `needs-review` (terminal), not processed |
| **H2** | **OCR → Markdown review** (`mdreview_ui_handler`, `/mdreview` UI) — **BUILT + TESTED, NOT DEPLOYED** (commit `d1c9869`) | OCR of a scanned page needs verification (the OCR+AI markdown is the *source of truth* for reference material) | two-pane page-image + editable snippet; save to `ocr-output/{book}/reviewed/pN.md` | merge-time **consume-once substitution** in `merge_outputs` — reviewed page replaces the raw OCR page before the chapter markdown is written. **Indefinite human pause** (like dedup); NAT torn down while waiting, resumed on completion |
| H3 | **Structure detection** (`markdown_structure`, `web_video`) | weak/ambiguous detection (block quotes, flattened tables, pending video transcription) → `needs_review=True` | confirm/correct the span | only `needs_review=False` spans are auto-applied; weak ones await confirmation |
| H4 | **OOB reference parsing** (`oob_markdown/*`) | suspect cell (empty/garbled/unknown) or fuzzy name crosswalk → `needs_review` + `review_count` | verify the flagged rows/links | verification-only (never auto-corrected); low confidence recorded |
| H5 | **Dedup review gate** (`dedup_ui_handler`, `/dedup` web UI) | duplicate groups detected in Phase 2 (`duplicate_report.json` for people/places/groups/equipment) | merge / skip / exclude each group, then `POST /dedup/api/complete` | **ungates Phase 3** — enrichment is blocked until review is marked done |
| H6 | **Doc off-ramp** (`_offramp_doc_needs_review`, `ecs_entrypoint`) | dedup gate blocks a doc in multi-doc mode | review the doc | doc lifecycle → `needs-review`; dispatcher stops advancing it |
| H7 | **Operator alerts** (both email + Slack) | phase completion, financial/quota warnings, failures | acknowledge / act | informational + actionable deep links; some (quota ≥80%) prompt action |

Two **blocking human gates** are indefinite async pauses, and NAT follows the
same lifecycle for both (UP across compute, **DOWN while waiting on a reviewer**,
resumed on completion):
- **H2 — OCR→Markdown review** (built, not yet deployed): reviewed pages
  substitute into the merge; the OCR+AI markdown is the source of truth for
  scanned reference material.
- **H5 — Dedup review**: Phase 2 duplicate reports resolved via the web UI;
  only then is Phase 3 ungated.

Everything else (H1, H3, H4, H6) is a **review off-ramp** — flagged items divert
to `needs-review` while the rest of the corpus flows on.



All branches converge on Markdown in `contentrepository/{book}/chapter{N}/`
(content + `-meta.yaml`). From there:

### Phase 1 — Parse (`phase1_parse.py`) → structured JSON per chapter

Deterministic, no LLM. `discover_content_structure` walks the book/chapter
layout; `parse_chapter` turns each Markdown chapter into a serializable
document. **Produces** (one JSON per chapter) with:
- **Metadata**: `book`, `chapter_number`, `chapter_title`, `section_id`,
  `author`, `series`, `license`, `source_file`.
- **Paragraphs**: ordered, each with `absolute_number`, `text`, `page_number`,
  `section_id`, `source_file`, and quote flags (`is_quote`,
  `quote_attribution`) — this is the unit of provenance later extraction cites.
- **Images / maps / footnotes**: `resource_id`/`url`/`alt_text`/`caption`,
  `map_id`, footnote `number`→`url`.
- **`table_hints`**: spans of flattened/scanned tables for downstream repair.

This is the citable substrate: page/paragraph numbers + verbatim text, no
interpretation yet.

### Phase 2 — Extract (`phase2_extract.py`) → 11 cross-referenced entity types

LLM extraction via the **Grok Batch API** (50% discount). Runs in stages:
metadata completion (fills incomplete `-meta.yaml` via Grok) →
`extract_events` (the event-centric spine) → the typed entity extractors →
`import_maps` → **dedup reports** (`find_duplicate_people`,
`find_related_groups`). **Produces** JSON keyed by ULID, all linked back to
events via `event_mentions` (the junction that carries book/author/series +
`MentionID` + page context):

| Type | Output | Notable fields |
|------|--------|----------------|
| **Events** | `output/events/*.json` | `EventID`, `Sub-events[]` each with entity-ID arrays (dates/places/people/groups) |
| **Dates** | `output/dates/` | `date_start/end`, `date_precision`, `time_*`, `original_text` |
| **Places** | `output/places/` | `geography_type`, `coordinates` (+`confidence`), `hierarchy`, `map_urls`, `related_places` |
| **People** | `output/people/` | `biographical_profile` (ranks/units/awards) — mostly filled in Phase 3 |
| **People Groups** (units/orgs) | `output/people_groups/` | `group_type`, `military_hierarchy`, `parent_organization`, `members[]` |
| **Weather** | `output/weather/` | deduped by date+location; `extracted_data` (+ api in P3) |
| **Equipment, Logistics, Casualties, Maps, Citations** | `output/…` | typed per schema; Maps/Citations carry source + `verbatim_reference` |

Key property: **event-centric with ULID cross-refs** — an event's sub-event
points at DateID/PlaceID/PersonID/GroupID; each entity carries `event_mentions`
back to the event + source. Dedup runs at the front door (title→ref→author for
bibliography); Phase 2 **entity** duplicates go through the **human dedup review
gate (H5)** before Phase 3 is ungated (see Human intervention points).

### Phase 3 — Enrich (`phase3_enrich_data.py`) → external data merged in

Per-entity enrichment from external sources, merged into the Phase 2 JSON
(never re-extracted):
- **People** (`enrich_all_people`): OpenSERP images + academic/oral-history
  references; `biographical_profile` (birth/death, nationality, ranks,
  units_served, awards) with `biography_sources` + confidence; sets
  `enrichment_status`/`openserp_searched`.
- **Groups** (`enrich_all_groups`): `enrichment_data` (formed/disbanded,
  commanding_officers, notable_operations), resolved `members[]`.
- **Places** (`enrich_all_places` + `link_parent_place_ids`): geocode
  lat/long, hierarchy, bounding box, map URLs; links parent places.
- **Bibliography** (`enrich_bibliography`): resolves/cleans citation entries so
  they point at real sources.
- **Weather**: hybrid extracted + Open-Meteo API where dated+located.

Enrichment is idempotent per the steering discipline (track processed state;
don't re-enrich unchanged content), and emits SNS progress
(Phase 3 in-progress / complete) to both email + Slack.



- `action=verify` → `{"ready": true, "missing": []}` with all 7 endpoints
  available.
- OCR job `3283f45d-…`: RUNNABLE → STARTING (`registered=1`) → RUNNING;
  container log shows PDF downloaded, `Model loaded successfully`,
  `Loaded 11 page(s)`, `Processing pages 1-1...`.
- Gate: `scripts/gate.sh` PASS, 1161 tests (commits `4bb5ee0`, `561e71f`).

## IAM compliance audit (2026-09-29)

Triggered by a silent-deny bug: nat_manager's teardown guard was **blind**
because its role lacked `batch:ListJobs` — every `list_jobs` call was denied and
swallowed by a per-status `except: continue`, so the guard read "no OCR demand"
and tore down NAT under a running job (twice). Audited every AWS API call each
Lambda makes against the actions its role grants (authoritative — read the
inline policies, since `simulate-principal-policy` false-negatives on
resource-scoped statements).

| Role | Handlers | Finding |
|------|----------|---------|
| `dev-wwii-lambda-role` (shared) | trigger, nat-manager, openserp-manager, batch-poller, ocr-merge, dedup-ui/gate/auth, mdreview-ui, slack-formatter, metrics | **GAP: `batch:ListJobs` missing** (only `batch:SubmitJob` granted). `SubmitJob` also scoped to the spot queue ARN only — missing the on-demand queue. **Both fixed** in `iam.yaml`. All other calls (ecs, sfn start/list, sns, secrets, ec2 nat/endpoints/routes, s3, dynamo, scheduler, ssm, cfn, lambda invoke) properly granted. |
| `dev-wwii-ocr-controller-role` | ocr-spot-controller | Clean — batch Submit/Describe/List/Terminate + servicequotas + sns + dynamo. |
| `dev-wwii-dispatcher-lambda-role` | enumerate-pending, clamp-pool, human-gate | Clean — ecs:ListTasks + servicequotas + sns + states SendTaskSuccess/Failure. |

**Defense-in-depth (code):** `_ocr_jobs_in_flight` now **fails safe** — any
error other than a definitive "queue does not exist" (e.g. `AccessDenied`,
throttling) is treated as *demand present*, never silently ignored. An IAM gap
can no longer silently disable a safety guard.

**Principle recorded:** a swallowed AWS error in a *safety-critical* check
(teardown guards, demand checks) must fail toward the safe state, not the
permissive one.

## Follow-ups

- **Multi-format routing gap (highest-value):** wire `trigger_handler`
  (`_split_by_media`) to the intended per-media-type routing so images go to
  OCR and docx/epub/html/txt go through their pandoc/`convert_to_markdown`
  step, instead of the current pdf-vs-rest split that sends non-md straight to
  parse. Converters already exist in `src/ingestion/`; defer to `route_file` /
  `_ROUTING`. Needs tests per format.
- EIP release `AuthFailure` on teardown (IAM perm gap) — minor, cleanup only.
- Full-chain close (#5): confirm merge → chapter structure → parse after this
  job SUCCEEDED; then a **true** front-door test (upload via S3 so the trigger
  does chunking + NAT-ensure + claim end-to-end).
- Tear down GPU/NAT after testing.
