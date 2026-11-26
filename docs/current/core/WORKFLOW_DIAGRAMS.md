# Pipeline Workflow Diagrams

Visual representations of the WWII data extraction pipeline phases.

**Last Updated:** 2026-11-17. The Mermaid source below is current for Phases 0-3 (checked against `lambda_handlers/trigger_handler.py`, `phase1_parse.py`, `phase2_extract.py`, `phase3_enrich_data.py`, `src/utils/batch_rounds.py`). The PNGs in `images/` are rendered from these blocks by `scripts/render_mermaid_diagrams.py` (it re-renders only a diagram whose source hash in `images/mermaid_manifest.json` changed; set `MERMAID_NO_SANDBOX=1` where the OS blocks Chrome's sandbox). Re-run it after editing a diagram.

---

## Phase 0: Ingestion (before Phase 1)

```mermaid
flowchart TD
    Upload([Upload to contentrepository/]) --> Trigger["trigger Lambda<br/>(lambda_handlers/trigger_handler.py)"]
    Trigger --> Slug{"Safe slug?<br/>(book_slug: filename stem)"}
    Slug -->|No / renamed re-upload| Review["needs-review/<br/>(unnamed-upload, source-renamed)"]
    Slug -->|Yes| AV["ClamAV Fargate scan of binaries<br/>(pdf, image, video, office; fail-closed,<br/>infected -> quarantine; text/md skip it)"]
    AV --> Route{Media type}

    Route -->|PDF / scan| Chunk["Split into page chunks<br/>manifest written BEFORE submit"]
    Chunk --> Chandra["Chandra OCR on AWS Batch GPU<br/>ocr-output/slug/chunk-pNNNN-NNNN/"]
    Chandra --> Merge["ocr_merge Lambda waits for ALL chunks<br/>then ocr_promote.promote"]
    Merge --> Promote["sanitise, translate to English per page,<br/>split into chapters, chandra_markdown normalise<br/>(pictures removed from text, kept as figures)"]
    Promote --> Cross{"Validation URL<br/>on the upload?"}
    Cross -->|Yes| XCheck["phase0_ocr_crosscheck ECS task"]
    Cross -->|No| Publish
    XCheck --> Publish

    Route -->|EPUB / DOCX / HTML| Pandoc["phase0_convert (pandoc)"]
    Route -->|Video| Video["phase0_video"]
    Route -->|Structured reference doc| OOB["phase0_ingest (OOB parser track)"]
    Pandoc --> Publish
    Video --> Publish

    Publish["Publish contentrepository/slug/chapterN/<br/>chapterN-meta.yaml + chapterN-content.md<br/>(book metadata found by Grok, with evidence quotes)"] --> P1([Phase 1])

    style Upload fill:#90EE90
    style P1 fill:#90EE90
    style Chandra fill:#FFB6C1
    style Review fill:#F0A0A0
    style Promote fill:#87CEEB
```

**Key operations:**
- A book's slug is its filename stem and never changes (`src/ingestion/book_slug.py`).
- Bad uploads go to `needs-review/`; nothing is processed under a shared `unknown` name.
- OCR is chunked by pages; the merge Lambda acts only when every chunk of the submission is present.

---

## Phase 1: Parse Workflow

```mermaid
flowchart TD
    Start([Start Phase 1]) --> LoadConfig[Load config.yaml]
    LoadConfig --> Discover["discover_content_structure:<br/>contentrepository/book/chapterN/<br/>(skips sourcedocument/ and _figures/)"]
    Discover --> ForEach{For each<br/>chapter}
    ForEach --> ReadMD["Read chapterN-content.md<br/>+ chapterN-meta.yaml"]
    ReadMD --> ParseChapters["Parse paragraphs, footnotes/endnotes,<br/>page markers, images, maps;<br/>lift Phase 0 translation markers -> translated_from"]
    ParseChapters --> NumberParas["Number paragraphs absolutely<br/>(absolute_number + page_number)"]

    NumberParas --> OCR{"OCR'd PDF book?"}
    OCR -->|Yes| Figures["book_figures.ensure: build the book's own figures ONCE<br/>(PDF + OCR crops -> _figures/figures.json);<br/>rebuilt only if the PDF or OCR submission changes"]
    OCR -->|"No (web / HyperWar)"| Links["Images and maps from links in the text"]
    Figures --> Attach["attach_book: each picture to ONE chapter by PDF page;<br/>each numbered map to every chapter that prints or cites it"]
    Attach --> SizeCheck
    Links --> SizeCheck

    SizeCheck{"Chapter text<br/>>400k chars?"}
    SizeCheck -->|Yes| AutoSplit["Split into 50-paragraph chunks<br/>(3-paragraph overlap)"]
    SizeCheck -->|No| BuildJSON
    AutoSplit --> BuildJSON

    BuildJSON["Build JSON: metadata, paragraphs,<br/>notes, images, maps, page_markers"] --> Hash{"Source hash<br/>changed?"}
    Hash -->|No| Skip["Skip write / S3 upload"]
    Hash -->|Yes| WriteOutput["Write output/content/book/<br/>chapterN-parsed.json (+ footnotes / endnotes)"]
    Skip --> NextFile
    WriteOutput --> NextFile{More chapters?}
    NextFile -->|Yes| ForEach
    NextFile -->|No| End([Phase 1 Complete])

    style Start fill:#90EE90
    style End fill:#90EE90
    style AutoSplit fill:#FFE4B5
    style Figures fill:#FFE4B5
    style BuildJSON fill:#87CEEB
```

**Key Operations:**
- Markdown → JSON conversion with absolute paragraph numbering and page numbers
- Book figures built once per OCR'd PDF book, then attached per chapter
- Auto-splitting of chapters over 400K characters
- Unchanged chapters are not rewritten or re-uploaded

---

## Phase 2: Extract Workflow

```mermaid
flowchart TD
    Start([Start Phase 2]) --> LoadConfig[Load config.yaml]
    LoadConfig --> CompleteMeta["Step 0: Complete missing metadata"]
    CompleteMeta --> InitGrok["Initialize Grok client + API cache<br/>(batch mode in AWS)"]
    InitGrok --> Round["<b>Batch round N</b> (cap batch.max_rounds = 8)"]

    Round --> Step1["<b>Step 1: Core extraction</b><br/>process_chapters_parallel"]
    Step1 --> Events["Events: ONE request per chapter<br/>(Event + Sub-events; orphan paragraphs claimed<br/>by nearest earlier sub-event)"]
    Events --> Emit["Emit source_section anchor<br/>(title + EventID)"]
    Emit --> Windows["Discovery over fixed 3-paragraph WINDOWS<br/>of the RAW parsed text (windows.py):<br/>Dates | Places | Groups | People"]
    Windows --> Settle["windows.settle: attribute each item to the<br/>real sub-event by where its quote is;<br/>unsupported quote -> dropped (quote_unsupported)"]
    Settle --> NativeEnrich["<b>Native Grokipedia/Wikipedia enrichment</b><br/>People: bio + portrait + awards | Groups | Places"]

    NativeEnrich --> Step2["Step 2: Retry missing events"]
    Step2 --> Bib["Step 3: Deterministic bibliography<br/>(one record per footnote/endnote, no LLM)"]
    Bib --> OptGate{"Optional features enabled?"}
    OptGate -->|Yes| Opt["Per event file, windowed:<br/>Weather | Equipment (broad + refine) |<br/>Logistics | Casualties | Supplemental (notes)"]
    OptGate -->|No| Maps
    Opt --> Maps["External maps (OpenSERP if enabled) + Images"]

    Maps --> Queued{"This pass queued<br/>new requests?"}
    Queued -->|Yes| Submit["Submit xAI batch, wait in-task<br/>up to batch.in_task_wait_minutes"]
    Submit --> Answered{Answered?}
    Answered -->|Yes| Round
    Answered -->|No| Poller["Hand off to batch-poller Lambda (5 min)<br/>-> launches --retrieve-only task"]
    Poller --> Round
    Queued -->|"No (quiescent)"| Step4b
    Queued -->|"Round cap reached"| CapAlert["alert batch.rounds_over_cap + stop"]

    Step4b["<b>Step 4b: Source-section enrichment</b><br/>summary + operation label (null-over-fake)<br/>+ Grok/Wikipedia articles and media"] --> Step5

    Step5["Step 5: Analysis: duplicate people + related groups reports"] --> Post["Exact-duplicate auto-merge,<br/>dedup report, provenance validation<br/>(scripts/validate_provenance.py)"]
    Post --> Gate["Dedup gate: auto-proceeds when no duplicate groups (corpus under 500 entities);<br/>else Phase 3 blocked until human review (Dedup Review UI)"]
    Gate --> End([Phase 2 Complete])
    CapAlert --> End

    style Start fill:#90EE90
    style End fill:#90EE90
    style Step1 fill:#87CEEB
    style NativeEnrich fill:#FFB6C1
    style Step4b fill:#FFB6C1
    style Step5 fill:#DDA0DD
    style CapAlert fill:#F0A0A0
```

**Key operations:**
- One Event per source section, one events request per chapter; every other entity is discovered over fixed 3-paragraph windows of the raw text and attributed to its real sub-event by its quote.
- **Phase 2 owns ALL Grokipedia/Wikipedia enrichment** (people, groups, places, equipment), committed through each entity's native path.
- Batch rounds repeat until a pass queues nothing; a batch not answered in-task is retrieved by the poller Lambda.
- Step 4b derives the section summary + canonical operation label, then fetches the operation's Grok/Wiki articles + Wikipedia media.
- Alerts go to the `{env}-wwii-phase2-complete` SNS topic (email + Slack).

---

## Phase 3: Enrich Workflow

```mermaid
flowchart TD
    Start([Start Phase 3]) --> LoadConfig[Load config.yaml]
    LoadConfig --> InitGrok[Initialize Grok client + API cache]
    InitGrok --> Note["Phase 3 = ALL external enrichment EXCEPT Grok/Wiki<br/>(Grokipedia/Wikipedia moved to Phase 2)"]

    Note --> S1["people_groups: structured unit/org history (Grok)"]
    S1 --> S2["places: hierarchy/names (structured) + link parents"]
    S2 --> S3["places: geocoding cascade<br/>Nominatim → hill/terrain → Grok (coords + provenance)"]
    S3 --> S4["bibliography: ISBN/copyright, then NARA & non-NARA resolution<br/>(Archive.org, Gutenberg, OpenSERP, Grok)"]
    S4 --> Media["Document media: in_doc_map_links, image_map_vision<br/>(independent of OpenSERP)"]
    Media --> S5Gate{use_openserp?<br/>forced on in ECS}

    S5Gate -->|Yes| S5["<b>OpenSERP group</b>"]
    S5Gate -->|No| Eq
    S5 --> O1["people / equipment / groups / places / source_section<br/>(+ external maps if enabled)"]
    O1 --> OQ["Queries: entity-fact-sharpened,<br/>multi-language (places), operation-keyed (source_section)"]
    OQ --> OV["Grok verify (FAIL-CLOSED: title+snippet+url)"]
    OV --> OR{Verified?}
    OR -->|No| ONeg["Cache URL verdict REJECT (90d) → skip"]
    OR -->|Yes, textual| OFetch["Fetch page → Grok summarize →<br/>store {url,title,summary,fetched_at}<br/>+ cache summary (90d)"]
    OR -->|Yes, image| OImg["Store image reference"]
    O1 --> NS["Non-search steps nested in the group:<br/>image_person_link, link_asset_review, image_place_link,<br/>intra_corpus_links, event_provenance_stamp"]
    OFetch --> Eq
    OImg --> Eq
    ONeg --> Eq
    NS --> Eq

    Eq["equipment_images: Grok vision confirms candidates<br/>for SPECIFIC equipment (not gated by use_openserp)"] --> NoaaGate{"api.noaa_api_token<br/>set?"}
    NoaaGate -->|Yes| S6["weather: NOAA GHCND observed"]
    NoaaGate -->|No| Summary
    S6 --> Summary["Per-source stats → .phase_results.json<br/>→ email/Slack (isolated: one failure never aborts)"]
    Summary --> End([Phase 3 Complete])

    style Start fill:#90EE90
    style End fill:#90EE90
    style Note fill:#FFE4B5
    style S5 fill:#87CEEB
    style OFetch fill:#FFB6C1
    style ONeg fill:#F0A0A0
```

**Key operations:**
- No Grokipedia/Wikipedia here — that is Phase 2. Phase 3 keeps geocoding, NOAA, OpenSERP, and
  NARA + non-NARA bibliography resolution, plus the *structured* hierarchy/org-history Grok
  passes (which promote facts into spec fields, not descriptive text).
- The OpenSERP group covers people, equipment, groups, places and source_section with fact-sharpened / multi-language / operation-keyed queries. Dates, casualties, logistics and events have no search step.
- Positive URLs are Grok-verified **fail-closed**, then textual pages are fetched + summarized;
  every URL verdict (summary or REJECT) is cached 90 days to prevent reprocessing.
- Each source isolated via `_run_step`; outcomes surface through `.phase_results.json`.
- Per-entity detail and known gaps: [PHASE3_MAP.md](PHASE3_MAP.md).

---

## Complete Pipeline Flow

```mermaid
flowchart LR
    Up["Uploads<br/>contentrepository/<br/>PDF / EPUB / DOCX / HTML / video"] --> Phase0["<b>Phase 0</b><br/>trigger Lambda, Chandra OCR,<br/>phase0_convert / video / ingest"]
    Phase0 --> Chapters["Chapters<br/>contentrepository/slug/chapterN/"]
    Chapters --> Phase1["<b>phase1_parse.py</b><br/>Parse"]
    Phase1 --> Parsed[Parsed JSON<br/>output/content/slug/*-parsed.json]

    Parsed --> P2Retry["<b>phase2_retry.py</b><br/>Auto-retry wrapper"]
    P2Retry --> Phase2["<b>phase2_extract.py</b><br/>Extract (xAI batch rounds)"]
    Phase2 --> Events[Events<br/>*-event.json]
    Phase2 --> Core[Core Entities<br/>dates/ places/<br/>people/ people_groups/]
    Phase2 --> Optional[Optional Entities<br/>weather/ equipment/<br/>logistics/ casualties/<br/>bibliography/]
    Phase2 --> Maps[Maps<br/>maps/ external_maps/]
    Phase2 --> Gate["Dedup review gate<br/>+ provenance validation"]

    Gate --> P3Retry["<b>phase3_retry.py</b><br/>Auto-retry wrapper"]
    P3Retry --> Phase3["<b>phase3_enrich_data.py</b><br/>Enrich"]
    Phase3 --> Enriched[Enriched JSON<br/>entities updated in place]

    style Phase0 fill:#FFE4B5
    style Phase1 fill:#87CEEB
    style Phase2 fill:#90EE90
    style Phase3 fill:#FFB6C1
    style Gate fill:#DDA0DD
    style P2Retry fill:#FFE4B5
    style P3Retry fill:#FFE4B5
```

In AWS, entity records live in S3 (`output/<entity>/*.json`) and run state in the DynamoDB `{env}-wwii-api-cache` table. The former MongoDB and DynamoDB import steps were removed (`import_to_mongodb.py`, `import_to_dynamodb.py`); merge pointers live in `{env}-wwii-id-aliases`.

---

## Event Extraction Detail

```mermaid
flowchart TD
    Start([Event Extraction]) --> ParseText["Parsed chapter<br/>(absolute paragraph numbers)"]
    ParseText --> IdentifyEvents["ONE events request per chapter<br/>(Grok; answer must echo BookID + RequestID)"]

    IdentifyEvents --> Valid{"Valid and covers<br/>the chapter?"}
    Valid -->|"Truncated"| Split["Auto-split the chapter in parts<br/>(_split_on_truncation) and merge"]
    Valid -->|"Low coverage"| Reask["Re-ask once, else split"]
    Valid -->|Yes| CreateEvent
    Split --> CreateEvent
    Reask --> CreateEvent

    CreateEvent["Create Event:<br/>- EventID = deterministic ULID<br/>  event_id(book, chapter, section)<br/>- name, summary"] --> CreateSubEvents["Create Sub-events:<br/>- Sub-eventID = deterministic ULID<br/>  from the paragraph range<br/>- summary, paragraph numbers<br/>Orphan paragraphs claimed by the nearest earlier sub-event"]

    CreateSubEvents --> ExtractEntities["Entity discovery over fixed 3-paragraph windows<br/>of the RAW parsed text (never the summaries)"]
    ExtractEntities --> Attribute["windows.settle: attribute each item to the sub-event<br/>that holds its quote (exact / repaired / sentence naming the entity / unquoted);<br/>otherwise dropped: quote_unsupported"]
    Attribute --> Entities["Dates | Places | People | Groups |<br/>Equipment | Weather | Logistics | Casualties |<br/>Supplemental (notes -> bibliography)"]
    Entities --> Mentions["One mention per sub-event + normalised quote<br/>(entity_records.mention_identity);<br/>entity ids are ulid.new(), event ids are derived"]
    Mentions --> SaveEvent[Save event JSON]
    SaveEvent --> SaveEntities[Save entity JSON files + indexes]
    SaveEntities --> End([Extraction Complete])

    style Start fill:#90EE90
    style End fill:#90EE90
    style CreateEvent fill:#87CEEB
    style ExtractEntities fill:#FFE4B5
    style Attribute fill:#DDA0DD
```

---

## Entity Deduplication Flow

```mermaid
flowchart TD
    Start(["Deduplication (end of Phase 2)"]) --> Score["Score candidate pairs per entity<br/>(src/dedup/find_duplicate_*.py, weights in config dedup:)<br/>name similarity, shared unit/events,<br/>quote proximity, surname frequency"]
    Score --> Exact{"Exact duplicate?<br/>(identical normalised name)"}
    Exact -->|Yes| AutoMerge["Auto-merge<br/>(ecs_entrypoint._auto_merge_exact_duplicates,<br/>src/dedup/merge.py)"]
    Exact -->|No| Report["Dedup report:<br/>candidates above candidate_threshold"]
    AutoMerge --> Report
    Report --> GateQ{"Candidates left?"}
    GateQ -->|No| Open["Gate open: Phase 3 starts"]
    GateQ -->|Yes| Block["Dedup gate: Phase 3 blocked,<br/>operator notified (email + Slack)"]
    Block --> UI["Human review in the Dedup Review UI<br/>(lambda_handlers/dedup_ui_handler.py):<br/>merge, or mark 'not duplicates'<br/>(src/dedup/exclusions.py)"]
    UI --> Open
    Open --> Groups["Related-groups report:<br/>parent-child / sibling units linked"]
    Groups --> End([Deduplication Complete])

    style Start fill:#90EE90
    style End fill:#90EE90
    style AutoMerge fill:#FFB6C1
    style UI fill:#87CEEB
    style Block fill:#FFE4B5
```

---

## Validation Workflow

```mermaid
flowchart TD
    Start([Validation]) --> LoadSchema[Load JSON schemas]
    LoadSchema --> ScanOutput[Scan output/ directory]
    
    ScanOutput --> ForEachFile{For each<br/>JSON file}
    ForEachFile --> DetectType[Detect entity type:<br/>- Event<br/>- Date<br/>- Place<br/>- Person<br/>- Group<br/>- etc.]
    
    DetectType --> GetValidator[Get validator<br/>from registry]
    GetValidator --> PreHooks[Run pre-validation hooks]
    PreHooks --> Validate[Validate against schema]
    
    Validate --> Valid{Valid?}
    Valid -->|Yes| PostHooks[Run post-validation hooks]
    Valid -->|No| CollectErrors[Collect errors:<br/>- Field name<br/>- Error message<br/>- Line number]
    
    PostHooks --> NextFile
    CollectErrors --> NextFile{More<br/>files?}
    
    NextFile -->|Yes| ForEachFile
    NextFile -->|No| GenerateReport[Generate report:<br/>- Files validated<br/>- Errors found<br/>- Success rate]
    
    GenerateReport --> End([Validation Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style Validate fill:#87CEEB
    style CollectErrors fill:#FFB6C1
    style GenerateReport fill:#FFE4B5
```

---

## Batch Processing Flow

```mermaid
flowchart TD
    Start([Phase 2 in batch mode]) --> LoadFiles[Load parsed chapters]
    LoadFiles --> Round["Round N: run every step with a<br/>batch-collecting GrokClient"]
    Round --> Collect["Each request is queued instead of sent<br/>(BatchModeCollecting); cached answers are used"]
    Collect --> Any{"New requests<br/>queued this round?"}
    Any -->|No| Done([Quiescent: finish steps 4b-5])
    Any -->|Yes| Cap{"Round > batch.max_rounds (8)?"}
    Cap -->|Yes| Alert["Alert batch.rounds_over_cap<br/>+ diagnostics, stop"]
    Cap -->|No| Submit["submit_batch -> xAI Batch API<br/>(batch_job# row in DynamoDB)"]
    Submit --> Wait["Task waits up to<br/>batch.in_task_wait_minutes (5)"]
    Wait --> Answered{Answered?}
    Answered -->|Yes| Store["store_json_answer into the cache"]
    Answered -->|No| Handoff["Task exits (75 = handed off);<br/>batch-poller Lambda (5 min schedule)<br/>launches a --retrieve-only task"]
    Handoff --> Store
    Store --> Round

    style Start fill:#90EE90
    style Done fill:#90EE90
    style Submit fill:#87CEEB
    style Handoff fill:#FFE4B5
    style Alert fill:#F0A0A0
```

**Key Design:**
- Work that needs an earlier answer (for example equipment refine needs the broad answer) is asked in the next round; rounds repeat until a pass queues nothing.
- Answers come back through the same cache keys, so a re-run never re-asks (`src/utils/batch_rounds.py`, `src/utils/batch_api.py`).
- A chapter is processed concurrently with up to `max_parallel` others (`process_chapters_parallel`), and a failure in one chapter or entity does not stop the others (`return_exceptions=True`).
- Targeted cache clearing on failure (per entry, not a full wipe).

---

## Cache Management Flow

```mermaid
flowchart TD
    Start([API Call]) --> MakeKey["Generate cache key<br/>sha256(prompt + temp + model)"]
    MakeKey --> CheckCache{Key in<br/>cache?}
    CheckCache -->|Yes| ReturnCached[Return cached response]
    CheckCache -->|No| MakeRequest[Make API request]
    
    MakeRequest --> CheckError{API<br/>error?}
    CheckError -->|Yes| Retry["Retry with backoff:<br/>5 attempts, 4s→60s<br/>HTTP 429 Retry-After"]
    CheckError -->|No| Sanitize["Sanitize response:<br/>Strip control chars<br/>Fix invalid escapes"]
    
    Retry --> RetrySuccess{Success?}
    RetrySuccess -->|Yes| Sanitize
    RetrySuccess -->|No| Fail([API Failed])
    
    Sanitize --> SaveCache[Save to cache]
    SaveCache --> ParseJSON[Parse JSON response]
    
    ParseJSON --> ParseOK{Valid<br/>JSON?}
    ParseOK -->|Yes| ReturnResponse[Return parsed response]
    ParseOK -->|No| CheckTruncated{"Truncated?<br/>(< 100K chars)"}
    
    CheckTruncated -->|Yes| AutoClear["Auto-clear cache entry<br/>cache.pop(key)"]
    CheckTruncated -->|No| ManualSplit[Log: manual split needed]
    
    AutoClear --> RepairJSON["Try JSON repair:<br/>Close brackets<br/>Fix trailing commas"]
    ManualSplit --> Fail
    
    RepairJSON --> Repaired{Repaired?}
    Repaired -->|Yes| ReturnResponse
    Repaired -->|No| Fail
    
    ReturnCached --> End([Response])
    ReturnResponse --> End
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style Fail fill:#FFB6C1
    style ReturnCached fill:#87CEEB
    style SaveCache fill:#FFE4B5
    style AutoClear fill:#FFB6C1
    style RepairJSON fill:#DDA0DD
```

**Key Design:**
- Cache key = sha256 of system prompt + prompt + temperature + model (plus the book scope for book-bound kinds)
- Per-type cache directories: `cache/api/{events,dates,places,people,...}`
- Auto-clear corrupted entries (truncated/short responses) — no manual intervention
- JSON repair attempted before failing
- Retry with exponential backoff + HTTP 429 Retry-After support

---

## MongoDB Import Flow (removed)

The MongoDB import script (`import_to_mongodb.py`) no longer exists, so its diagram was removed. Entity records are stored as JSON in S3 (`output/<entity>/*.json`); There is no importer: S3 is the record store. Its PNG is archived at `docs/archive/2026-11-16/core/images/mongodb_import_flow.png`.

---

## Error Handling & Recovery Flow

```mermaid
flowchart TD
    Error([Error Occurs]) --> Classify{Error Type}

    Classify -->|Truncated JSON| Truncated["Answer starts as JSON and is cut short"]
    Classify -->|"Short / no JSON"| Short["Under 500 chars and invalid,<br/>or no JSON anywhere"]
    Classify -->|API Error| APIErr["HTTP 429 / 500 / timeout"]
    Classify -->|File I/O| FileErr["OSError / IOError"]
    Classify -->|JSON Parse| JSONErr["JSONDecodeError in the middle"]

    Truncated --> AutoClear1["Evict the cache entry<br/>(always, any length)"]
    AutoClear1 --> Size{"Over 100K chars?"}
    Size -->|Yes| AutoSplit["Likely max_tokens: split the chapter<br/>at section boundaries (events)"]
    Size -->|No| Retry1["Transient: retried"]
    AutoSplit --> ExtractChunks["Extract each chunk separately"]
    ExtractChunks --> MergeResults["Merge sub-events into single output"]
    MergeResults --> Continue

    Short --> AutoClear2["Evict + re-ask once"]
    AutoClear2 --> Continue

    APIErr --> Backoff["tenacity backoff:<br/>5 attempts, exponential 4s to 60s"]
    Backoff --> Recovered{Recovered?}
    Recovered -->|Yes| Continue[Continue processing]
    Recovered -->|No| FailChapter["Fail chapter<br/>(others continue)"]

    JSONErr --> Repair["_try_repair_json:<br/>merge concatenated objects,<br/>fix invalid escapes"]
    Repair --> Fixed{Repaired?}
    Fixed -->|Yes| Continue
    Fixed -->|No| Loud["Evict, OBSERV grok.json_failed,<br/>raise GrokParseError"]
    FileErr --> LogFile["Log with file name<br/>+ continue to next entity"]

    Retry1 --> NextRun["Next round / run: fresh API call"]
    Loud --> NextRun
    FailChapter --> RetryStep["Step 2: Retry missing events<br/>Per-chapter cache clear"]
    RetryStep --> NextRun

    style Error fill:#FFB6C1
    style AutoClear1 fill:#FFE4B5
    style AutoClear2 fill:#FFE4B5
    style AutoSplit fill:#FFE4B5
    style Continue fill:#90EE90
    style NextRun fill:#90EE90
    style FailChapter fill:#FFB6C1
    style RetryStep fill:#87CEEB
```

**Key Principles:**
- Errors are isolated: one chapter/entity failure doesn't stop the pipeline
- Cache corruption evicts the specific entry (not the whole cache)
- Truncated events answers over 100K chars split the chapter at section boundaries and re-extract
- The retry step catches anything missed in the parallel phase
- A total JSON loss is never silent: it logs `OBSERV grok.json_failed`

See [JSON_REPAIR.md](JSON_REPAIR.md) and [CACHE_AUTO_RECOVERY.md](CACHE_AUTO_RECOVERY.md).

---

## Legend

```mermaid
flowchart LR
    Start([Start/End]) --> Process[Process Step]
    Process --> Decision{Decision Point}
    Decision -->|Yes| Action1[Action]
    Decision -->|No| Action2[Alternative]
    
    style Start fill:#90EE90
    style Process fill:#87CEEB
    style Action1 fill:#FFE4B5
    style Action2 fill:#FFB6C1
```

**Colors:**
- 🟢 Green: Start/End points
- 🔵 Blue: Core processing steps
- 🟡 Yellow: Optional/alternative steps
- 🔴 Pink: API calls/external operations
- 🟣 Purple: Data operations (merge, deduplicate, index)

---

**Generated:** 2026-03-15  
**Updated:** 2026-11-17 (Phase 3 and Phase 1 diagrams revised; Phases 0-2, dedup, batch, error flows checked 2026-11-16)  
**Pipeline Version:** 2.0
