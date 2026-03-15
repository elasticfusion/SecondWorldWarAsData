# Pipeline Workflow Diagrams

Visual representations of the WWII data extraction pipeline phases.

---

## Phase 1: Parse Workflow

```mermaid
flowchart TD
    Start([Start Phase 1]) --> LoadConfig[Load config.yaml]
    LoadConfig --> ScanRepo[Scan contentrepository/]
    ScanRepo --> FindMD[Find .md files]
    
    FindMD --> ForEach{For each<br/>markdown file}
    ForEach --> ReadMD[Read markdown content]
    ReadMD --> ExtractMeta[Extract metadata<br/>from frontmatter]
    ExtractMeta --> ParseChapters[Parse chapters<br/>and sections]
    
    ParseChapters --> SplitCheck{Chapter<br/>>50 paragraphs?}
    SplitCheck -->|Yes| AutoSplit[Auto-split into<br/>sub-chapters]
    SplitCheck -->|No| NumberParas[Number paragraphs<br/>absolutely]
    AutoSplit --> NumberParas
    
    NumberParas --> BuildJSON[Build JSON structure:<br/>- Metadata<br/>- Chapters<br/>- Paragraphs<br/>- Absolute numbering]
    
    BuildJSON --> WriteOutput[Write to output/parsed/]
    WriteOutput --> NextFile{More files?}
    NextFile -->|Yes| ForEach
    NextFile -->|No| Summary[Generate summary:<br/>- Files processed<br/>- Chapters parsed<br/>- Paragraphs numbered]
    
    Summary --> End([Phase 1 Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style AutoSplit fill:#FFE4B5
    style BuildJSON fill:#87CEEB
```

**Key Operations:**
- Markdown → JSON conversion
- Absolute paragraph numbering
- Auto-splitting large chapters
- Metadata extraction

---

## Phase 2: Extract Workflow

```mermaid
flowchart TD
    Start([Start Phase 2]) --> LoadConfig[Load config.yaml]
    LoadConfig --> CompleteMeta[Complete missing metadata]
    CompleteMeta --> InitGrok[Initialize Grok client<br/>+ API cache]

    InitGrok --> Step1["<b>Step 1: Parallel Core Extraction</b><br/>max 3 chapters concurrent"]

    Step1 --> Batch{For each batch<br/>of chapters}
    Batch --> Ch1[Chapter A<br/>async]
    Batch --> Ch2[Chapter B<br/>async]
    Batch --> Ch3[Chapter C<br/>async]

    Ch1 --> Events1[Extract Events]
    Ch2 --> Events2[Extract Events]
    Ch3 --> Events3[Extract Events]

    Events1 --> Gather1["asyncio.gather:<br/>Dates | Places | Groups | People"]
    Events2 --> Gather2["asyncio.gather:<br/>Dates | Places | Groups | People"]
    Events3 --> Gather3["asyncio.gather:<br/>Dates | Places | Groups | People"]

    Gather1 --> Results[Collect results]
    Gather2 --> Results
    Gather3 --> Results

    Results --> Step2["<b>Step 2: Retry Missing Events</b><br/>Per-chapter cache clear + re-extract"]

    Step2 --> Step3{"Optional<br/>features<br/>enabled?"}
    Step3 -->|Yes| OptLoop["<b>Step 3: Optional Entities</b><br/>Sequential per event file"]
    Step3 -->|No| Step4

    OptLoop --> Weather[Weather]
    OptLoop --> Equipment[Equipment]
    OptLoop --> Logistics[Logistics]
    OptLoop --> Casualties[Casualties]
    OptLoop --> Supplemental[Supplemental]

    Weather --> Step4
    Equipment --> Step4
    Logistics --> Step4
    Casualties --> Step4
    Supplemental --> Step4

    Step4["<b>Step 4: Maps</b><br/>Source maps + External maps"] --> Step5

    Step5["<b>Step 5: Analysis</b><br/>Duplicate people report<br/>Related groups report"] --> End([Phase 2 Complete])

    style Start fill:#90EE90
    style End fill:#90EE90
    style Step1 fill:#87CEEB
    style Step2 fill:#FFE4B5
    style OptLoop fill:#FFE4B5
    style Step4 fill:#DDA0DD
    style Step5 fill:#DDA0DD
    style Gather1 fill:#87CEEB
    style Gather2 fill:#87CEEB
    style Gather3 fill:#87CEEB
```

**Key Operations:**
- Parallel chapter processing (async/await)
- Batched API calls (4 entity types per chapter in parallel)
- Per-chapter cache clearing on retry (not full cache wipe)
- Optional entities run sequentially per event file
- Analysis reports generated at end

---

## Phase 3: Enrich Workflow

```mermaid
flowchart TD
    Start([Start Phase 3]) --> LoadConfig[Load config.yaml]
    LoadConfig --> InitGrok[Initialize Grok client<br/>+ API cache]
    InitGrok --> LoadPeople["Load people JSON files<br/>(skip index, duplicate_report,<br/>not_duplicates)"]
    
    LoadPeople --> ForEachPerson{For each<br/>person file}
    ForEachPerson --> GetName{Has<br/>name?}
    GetName -->|No| SkipPerson[Skip file]
    GetName -->|Yes| SearchGrok["Search Grokipedia<br/>HTTP GET with timeout"]
    
    SearchGrok --> GrokFound{Text<br/>found?}
    GrokFound -->|Yes| ExtractGrok["Grok AI: extract structured JSON<br/>birth/death, ranks, units,<br/>awards, education, family,<br/>source_urls"]
    GrokFound -->|No| SearchWiki
    ExtractGrok --> MergeGrok["Merge into bio_profile<br/>(simple fields, lists, family)"]
    MergeGrok --> SearchWiki
    
    SearchWiki["Search Wikipedia API"] --> WikiFound{Text<br/>found?}
    WikiFound -->|Yes| ExtractWiki["Grok AI: extract structured JSON<br/>(same schema + source_urls)"]
    WikiFound -->|No| CheckRefs
    ExtractWiki --> MergeWiki[Merge into bio_profile]
    MergeWiki --> CheckRefs
    
    CheckRefs{References<br/>enabled?}
    CheckRefs -->|Yes| FollowRefs["Follow up to 3 references<br/>Grokipedia → Wikipedia fallback"]
    CheckRefs -->|No| ValidateURLs
    FollowRefs --> MergeRefs[Merge reference data]
    MergeRefs --> ValidateURLs
    
    ValidateURLs{Source URLs<br/>returned?}
    ValidateURLs -->|Yes| FetchURLs["Fetch each URL<br/>(HTTP GET)"]
    ValidateURLs -->|No| CheckEnriched
    
    FetchURLs --> URLExists{HTTP 200?}
    URLExists -->|No| MarkBroken["Mark URL broken"]
    URLExists -->|Yes| GrokVerify["Submit page content to Grok:<br/>Is this about the person?<br/>Contains relevant bio data?"]
    
    GrokVerify --> Relevant{Relevant?}
    Relevant -->|Yes| StoreURL["Add to biography_sources<br/>(confidence: 0.9)"]
    Relevant -->|No| MarkIrrelevant["Discard URL"]
    
    MarkBroken --> CheckEnriched
    StoreURL --> CheckEnriched
    MarkIrrelevant --> CheckEnriched
    
    CheckEnriched{Any new<br/>data added?}
    CheckEnriched -->|No| LogNoData["Log: no new data found"]
    CheckEnriched -->|Yes| Validate["Validate with Person model<br/>(Pydantic)"]
    
    Validate --> ValidOK{Valid?}
    ValidOK -->|Yes| SaveJSON["Save updated person JSON<br/>in-place"]
    ValidOK -->|No| LogError["Log validation error<br/>+ skip save"]
    
    SaveJSON --> NextPerson
    LogNoData --> NextPerson
    LogError --> NextPerson
    SkipPerson --> NextPerson{More<br/>people?}
    
    NextPerson -->|Yes| ForEachPerson
    NextPerson -->|No| Summary["Summary:<br/>enriched / total people"]
    
    Summary --> End([Phase 3 Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style SearchGrok fill:#FFB6C1
    style SearchWiki fill:#FFB6C1
    style ExtractGrok fill:#87CEEB
    style ExtractWiki fill:#87CEEB
    style Validate fill:#DDA0DD
    style FollowRefs fill:#FFE4B5
    style FetchURLs fill:#FFB6C1
    style GrokVerify fill:#87CEEB
    style StoreURL fill:#90EE90
```

**Key Operations:**
- Two-source search: Grokipedia first, then Wikipedia (both results merged)
- Grok AI extracts structured biographical JSON from raw source text, including source URLs
- Reference following: up to 3 referenced entities searched for additional context
- URL validation: each source URL is fetched, then page content submitted to Grok to verify relevance
- Pydantic model validation before saving
- All API responses cached (Grok client cache)
- Currently people-only; weather/maps enrichment planned but not yet implemented

---

## Complete Pipeline Flow

```mermaid
flowchart LR
    MD[Markdown Files<br/>contentrepository/] --> Phase1["<b>phase1_parse.py</b><br/>Parse"]
    Phase1 --> Parsed[Parsed JSON<br/>*-parsed.json]
    
    Parsed --> P2Retry["<b>phase2_retry.py</b><br/>Auto-retry wrapper"]
    P2Retry --> Phase2["<b>phase2_extract.py</b><br/>Extract"]
    Phase2 --> Events[Events<br/>*-event.json]
    Phase2 --> Core[Core Entities<br/>dates/ places/<br/>people/ people_groups/]
    Phase2 --> Optional[Optional Entities<br/>weather/ equipment/<br/>logistics/ casualties/<br/>supplemental/]
    Phase2 --> Maps[Maps<br/>maps/ external_maps/]
    
    Events --> P3Retry["<b>phase3_retry.py</b><br/>Auto-retry wrapper"]
    Core --> P3Retry
    P3Retry --> Phase3["<b>phase3_enrich_data.py</b><br/>Enrich"]
    Phase3 --> Enriched[Enriched JSON<br/>Biographies in-place]
    
    Enriched --> Import["<b>import_to_mongodb.py</b><br/>Optional"]
    Optional --> Import
    Maps --> Import
    Import --> DB[(MongoDB<br/>Database)]
    
    style Phase1 fill:#87CEEB
    style Phase2 fill:#90EE90
    style Phase3 fill:#FFB6C1
    style Import fill:#DDA0DD
    style P2Retry fill:#FFE4B5
    style P3Retry fill:#FFE4B5
```

---

## Event Extraction Detail

```mermaid
flowchart TD
    Start([Event Extraction]) --> ParseText[Parse chapter text]
    ParseText --> IdentifyEvents[Identify events<br/>using Grok AI]
    
    IdentifyEvents --> CreateEvent[Create Event:<br/>- EventID ULID<br/>- Event name<br/>- Summary]
    
    CreateEvent --> HasSubEvents{Has<br/>sub-events?}
    HasSubEvents -->|Yes| CreateSubEvents[Create Sub-events:<br/>- Sub-eventID ULID<br/>- Summary<br/>- Paragraph refs]
    HasSubEvents -->|No| ExtractEntities
    CreateSubEvents --> ExtractEntities
    
    ExtractEntities[Extract Entities] --> ExtractDates[Extract Dates:<br/>- DateID ULID<br/>- Date string<br/>- Precision<br/>- Paragraph refs]
    
    ExtractDates --> ExtractPlaces[Extract Places:<br/>- PlaceID ULID<br/>- Name<br/>- GPS coordinates<br/>- Type]
    
    ExtractPlaces --> ExtractPeople[Extract People:<br/>- PersonID ULID<br/>- Name<br/>- Rank<br/>- Role<br/>- Unit]
    
    ExtractPeople --> ExtractGroups[Extract Groups:<br/>- GroupID ULID<br/>- Name<br/>- Type<br/>- Parent unit]
    
    ExtractGroups --> LinkEntities[Link Entities:<br/>- Event → Sub-events<br/>- Sub-events → Dates<br/>- Sub-events → Places<br/>- Sub-events → People<br/>- People → Groups]
    
    LinkEntities --> SaveEvent[Save event JSON]
    SaveEvent --> SaveEntities[Save entity JSON files]
    SaveEntities --> End([Extraction Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style CreateEvent fill:#87CEEB
    style ExtractEntities fill:#FFE4B5
    style LinkEntities fill:#DDA0DD
```

---

## Entity Deduplication Flow

```mermaid
flowchart TD
    Start([Deduplication]) --> LoadPeople[Load all people JSON]
    LoadPeople --> ComparePeople[Compare people:<br/>- Name similarity<br/>- Rank match<br/>- Unit match<br/>- Date overlap]
    
    ComparePeople --> FindDupes{Duplicates<br/>found?}
    FindDupes -->|Yes| MergePeople[Merge people:<br/>- Combine data<br/>- Update references<br/>- Keep one ULID]
    FindDupes -->|No| LoadPlaces
    MergePeople --> LoadPlaces
    
    LoadPlaces[Load all places JSON] --> ComparePlaces[Compare places:<br/>- Name similarity<br/>- GPS proximity<br/>- Type match]
    
    ComparePlaces --> FindPlaceDupes{Duplicates<br/>found?}
    FindPlaceDupes -->|Yes| MergePlaces[Merge places:<br/>- Combine data<br/>- Update references<br/>- Keep one ULID]
    FindPlaceDupes -->|No| LoadGroups
    MergePlaces --> LoadGroups
    
    LoadGroups[Load all groups JSON] --> CompareGroups[Compare groups:<br/>- Name similarity<br/>- Type match<br/>- Parent unit]
    
    CompareGroups --> FindGroupDupes{Related<br/>groups?}
    FindGroupDupes -->|Yes| LinkGroups[Link groups:<br/>- Parent-child<br/>- Sibling units<br/>- Update hierarchy]
    FindGroupDupes -->|No| Summary
    LinkGroups --> Summary[Generate report:<br/>- People merged<br/>- Places merged<br/>- Groups linked]
    
    Summary --> End([Deduplication Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style MergePeople fill:#FFB6C1
    style MergePlaces fill:#87CEEB
    style LinkGroups fill:#FFE4B5
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
    
    GenerateReport --> CreateDashboard[Create HTML dashboard]
    CreateDashboard --> End([Validation Complete])
    
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
    Start([Batch Processing]) --> LoadFiles[Load all parsed files]
    LoadFiles --> Partition["Partition into batches<br/>(max_parallel chapters)"]
    
    Partition --> ForEachBatch{For each<br/>batch}
    ForEachBatch --> Async["asyncio.gather<br/>per batch"]
    
    Async --> ChA[Chapter async]
    Async --> ChB[Chapter async]
    Async --> ChC[Chapter async]
    
    ChA --> EvA[Extract Events<br/>if needed]
    ChB --> EvB[Extract Events<br/>if needed]
    ChC --> EvC[Extract Events<br/>if needed]
    
    EvA --> GatherA["_batch_extract × 4:<br/>Dates | Places | Groups | People"]
    EvB --> GatherB["_batch_extract × 4:<br/>Dates | Places | Groups | People"]
    EvC --> GatherC["_batch_extract × 4:<br/>Dates | Places | Groups | People"]
    
    GatherA --> Collect[Collect results<br/>+ error isolation]
    GatherB --> Collect
    GatherC --> Collect
    
    Collect --> NextBatch{More<br/>batches?}
    NextBatch -->|Yes| ForEachBatch
    NextBatch -->|No| Summary["Summary:<br/>processed / failed<br/>dates / places / groups / people"]
    
    Summary --> End([Batch Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style Async fill:#87CEEB
    style GatherA fill:#87CEEB
    style GatherB fill:#87CEEB
    style GatherC fill:#87CEEB
    style Collect fill:#FFE4B5
```

**Key Design:**
- Two-level parallelism: chapters concurrent + entities concurrent within each chapter
- Shared `_batch_extract` helper for all 4 entity types (dates, places, groups, people)
- `return_exceptions=True` — one entity failure doesn't stop others
- Per-chapter error isolation — one chapter failure doesn't stop the batch
- Targeted cache clearing on failure (per-entry, not full wipe)

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
- Cache key = sha256 of prompt + temperature + model
- Per-type cache directories: `cache/api/{events,dates,places,people,...}`
- Auto-clear corrupted entries (truncated/short responses) — no manual intervention
- JSON repair attempted before failing
- Retry with exponential backoff + HTTP 429 Retry-After support

---

## MongoDB Import Flow

```mermaid
flowchart TD
    Start([MongoDB Import]) --> Connect[Connect to MongoDB]
    Connect --> CheckDB{Database<br/>exists?}
    CheckDB -->|No| CreateDB[Create database]
    CheckDB -->|Yes| LoadCollections
    CreateDB --> LoadCollections
    
    LoadCollections[Load collections:<br/>- events<br/>- dates<br/>- places<br/>- people<br/>- groups] --> LoadJSON[Load JSON files]
    
    LoadJSON --> ForEachFile{For each<br/>JSON file}
    ForEachFile --> ParseJSON[Parse JSON]
    ParseJSON --> ValidateDoc[Validate document]
    
    ValidateDoc --> Valid{Valid?}
    Valid -->|Yes| CheckExists{Document<br/>exists?}
    Valid -->|No| LogSkip[Log skip]
    
    CheckExists -->|Yes| UpdateDoc[Update document<br/>upsert=True]
    CheckExists -->|No| InsertDoc[Insert document]
    
    UpdateDoc --> NextFile
    InsertDoc --> NextFile
    LogSkip --> NextFile{More<br/>files?}
    
    NextFile -->|Yes| ForEachFile
    NextFile -->|No| CreateIndexes[Create indexes:<br/>- EventID<br/>- DateID<br/>- PlaceID<br/>- PersonID<br/>- GroupID]
    
    CreateIndexes --> Summary[Generate summary:<br/>- Documents inserted<br/>- Documents updated<br/>- Errors]
    
    Summary --> End([Import Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style InsertDoc fill:#87CEEB
    style UpdateDoc fill:#FFE4B5
    style CreateIndexes fill:#DDA0DD
```

---

## Error Handling & Recovery Flow

```mermaid
flowchart TD
    Error([Error Occurs]) --> Classify{Error Type}
    
    Classify -->|Truncated JSON| Truncated["Response < 100K chars?"]
    Classify -->|Short Response| Short["Response < 500 chars"]
    Classify -->|API Error| APIErr["HTTP 429 / 500 / timeout"]
    Classify -->|File I/O| FileErr["OSError / IOError"]
    Classify -->|JSON Parse| JSONErr["JSONDecodeError"]
    
    Truncated -->|Yes| AutoClear1["Auto-clear cache entry<br/>+ raise GrokAPIError"]
    Truncated -->|No| AutoSplit["Auto-split chapter at<br/>section boundaries"]
    AutoSplit --> ExtractChunks["Extract each chunk<br/>separately"]
    ExtractChunks --> MergeResults["Merge sub-events<br/>into single output"]
    MergeResults --> Continue
    
    Short --> AutoClear2["Auto-clear cache entry<br/>+ retry once"]
    
    APIErr --> Backoff["Exponential backoff<br/>5 attempts, 4s→60s"]
    Backoff --> Recovered{Recovered?}
    Recovered -->|Yes| Continue[Continue processing]
    Recovered -->|No| FailChapter["Fail chapter<br/>(others continue)"]
    
    FileErr --> LogFile["Log with file name<br/>+ continue to next entity"]
    JSONErr --> LogJSON["Log with file name<br/>+ continue to next entity"]
    
    AutoClear1 --> NextRun["Next run: fresh API call"]
    AutoClear2 --> NextRun
    
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
- Cache corruption auto-clears the specific entry (not the whole cache)
- Truncated responses auto-split the chapter at section boundaries and re-extract
- Retry step catches anything missed in the parallel phase
- All error messages include file context for debugging

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
**Pipeline Version:** 2.0
