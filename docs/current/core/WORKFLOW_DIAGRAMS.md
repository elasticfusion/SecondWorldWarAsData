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
    LoadConfig --> InitCache[Initialize API cache]
    InitCache --> LoadParsed[Load parsed JSON files]
    
    LoadParsed --> ForEachChapter{For each<br/>chapter}
    ForEachChapter --> CheckCache{Cache<br/>exists?}
    CheckCache -->|Yes| LoadCache[Load from cache]
    CheckCache -->|No| CallAPI[Call Grok API]
    
    CallAPI --> ExtractEvents[Extract Events<br/>with hierarchy]
    ExtractEvents --> SaveCache[Save to cache]
    SaveCache --> ProcessEvents
    LoadCache --> ProcessEvents[Process events]
    
    ProcessEvents --> ExtractCore[Extract Core Entities:<br/>- Dates<br/>- Places<br/>- People<br/>- Groups]
    
    ExtractCore --> CheckOptional{Optional<br/>features<br/>enabled?}
    CheckOptional -->|Yes| ExtractOptional[Extract Optional:<br/>- Weather<br/>- Equipment<br/>- Logistics<br/>- Maps]
    CheckOptional -->|No| SaveEntities
    ExtractOptional --> SaveEntities
    
    SaveEntities[Save Entities] --> SaveEvents[Save to output/events/]
    SaveEvents --> SaveDates[Save to output/dates/]
    SaveDates --> SavePlaces[Save to output/places/]
    SavePlaces --> SavePeople[Save to output/people/]
    SavePeople --> SaveGroups[Save to output/people_groups/]
    
    SaveGroups --> CheckOpt2{Optional<br/>enabled?}
    CheckOpt2 -->|Yes| SaveOptional[Save optional entities]
    CheckOpt2 -->|No| NextChapter
    SaveOptional --> NextChapter{More<br/>chapters?}
    
    NextChapter -->|Yes| ForEachChapter
    NextChapter -->|No| Dedup[Run deduplication:<br/>- Merge duplicate people<br/>- Merge duplicate places<br/>- Link related groups]
    
    Dedup --> Summary[Generate summary:<br/>- Events extracted<br/>- Entities created<br/>- API calls made<br/>- Cache hits]
    
    Summary --> End([Phase 2 Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style CallAPI fill:#FFB6C1
    style ExtractCore fill:#87CEEB
    style ExtractOptional fill:#FFE4B5
    style Dedup fill:#DDA0DD
```

**Key Operations:**
- Event extraction with hierarchy
- Entity extraction (dates, places, people, groups)
- Optional features (weather, equipment, logistics, maps)
- API caching
- Deduplication

---

## Phase 3: Enrich Workflow

```mermaid
flowchart TD
    Start([Start Phase 3]) --> LoadConfig[Load config.yaml]
    LoadConfig --> LoadPeople[Load people JSON files]
    
    LoadPeople --> ForEachPerson{For each<br/>person}
    ForEachPerson --> CheckBio{Has<br/>biography?}
    CheckBio -->|Yes| NextPerson
    CheckBio -->|No| SearchWiki[Search Wikipedia/<br/>Grokipedia]
    
    SearchWiki --> FoundBio{Biography<br/>found?}
    FoundBio -->|Yes| ExtractBio[Extract:<br/>- Birth/death dates<br/>- Biography text<br/>- Service info<br/>- Awards]
    FoundBio -->|No| MarkMissing[Mark as not found]
    
    ExtractBio --> ValidateURLs[Validate URLs]
    ValidateURLs --> UpdatePerson[Update person JSON]
    MarkMissing --> UpdatePerson
    UpdatePerson --> NextPerson{More<br/>people?}
    
    NextPerson -->|Yes| ForEachPerson
    NextPerson -->|No| CheckWeather{Weather<br/>enabled?}
    
    CheckWeather -->|Yes| EnrichWeather[Enrich weather data:<br/>- Historical API calls<br/>- Temperature<br/>- Conditions<br/>- Precipitation]
    CheckWeather -->|No| CheckMaps
    EnrichWeather --> CheckMaps
    
    CheckMaps{Maps<br/>enabled?}
    CheckMaps -->|Yes| SearchMaps[Search external maps:<br/>- Battle maps<br/>- Campaign maps<br/>- Strategic maps]
    CheckMaps -->|No| Summary
    SearchMaps --> ValidateMapURLs[Validate map URLs]
    ValidateMapURLs --> SaveMaps[Save to output/external_maps/]
    
    SaveMaps --> Summary[Generate summary:<br/>- People enriched<br/>- Biographies added<br/>- Weather data added<br/>- Maps found]
    
    Summary --> End([Phase 3 Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style SearchWiki fill:#FFB6C1
    style EnrichWeather fill:#87CEEB
    style SearchMaps fill:#FFE4B5
```

**Key Operations:**
- Wikipedia/Grokipedia biographical enrichment
- Historical weather data
- External map search
- URL validation

---

## Complete Pipeline Flow

```mermaid
flowchart LR
    MD[Markdown Files<br/>contentrepository/] --> Phase1[Phase 1<br/>Parse]
    Phase1 --> Parsed[Parsed JSON<br/>output/parsed/]
    
    Parsed --> Phase2[Phase 2<br/>Extract]
    Phase2 --> Events[Events JSON<br/>output/events/]
    Phase2 --> Entities[Entity JSON<br/>output/dates/<br/>output/places/<br/>output/people/<br/>output/people_groups/]
    
    Events --> Phase3[Phase 3<br/>Enrich]
    Entities --> Phase3
    Phase3 --> Enriched[Enriched JSON<br/>Updated entities<br/>with external data]
    
    Enriched --> Import[Import to MongoDB<br/>Optional]
    Import --> DB[(MongoDB<br/>Database)]
    
    style Phase1 fill:#87CEEB
    style Phase2 fill:#90EE90
    style Phase3 fill:#FFB6C1
    style Import fill:#DDA0DD
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
    Start([Batch Processing]) --> LoadConfig[Load config.yaml]
    LoadConfig --> CheckMode{Concurrent<br/>enabled?}
    
    CheckMode -->|Yes| InitPool[Initialize thread pool<br/>max_workers from config]
    CheckMode -->|No| Sequential[Sequential processing]
    
    InitPool --> LoadChapters[Load all chapter files]
    LoadChapters --> Partition[Partition into batches<br/>max_event_files limit]
    
    Partition --> ForEachBatch{For each<br/>batch}
    ForEachBatch --> ProcessParallel[Process chapters in parallel:<br/>- Each thread extracts events<br/>- Shared cache<br/>- Thread-safe writes]
    
    ProcessParallel --> MergeResults[Merge results:<br/>- Combine events<br/>- Deduplicate entities<br/>- Update references]
    
    MergeResults --> NextBatch{More<br/>batches?}
    NextBatch -->|Yes| ForEachBatch
    NextBatch -->|No| Summary
    
    Sequential --> ForEachChapter{For each<br/>chapter}
    ForEachChapter --> ProcessOne[Process chapter]
    ProcessOne --> NextChapter{More<br/>chapters?}
    NextChapter -->|Yes| ForEachChapter
    NextChapter -->|No| Summary
    
    Summary[Generate summary:<br/>- Processing time<br/>- Speedup vs sequential<br/>- Entities extracted] --> End([Batch Complete])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style ProcessParallel fill:#87CEEB
    style MergeResults fill:#FFE4B5
```

---

## Cache Management Flow

```mermaid
flowchart TD
    Start([API Call]) --> CheckCache{Cache<br/>exists?}
    CheckCache -->|Yes| LoadCache[Load from cache/<br/>diskcache]
    CheckCache -->|No| MakeRequest[Make API request]
    
    LoadCache --> ValidateCache{Cache<br/>valid?}
    ValidateCache -->|Yes| ReturnCached[Return cached response]
    ValidateCache -->|No| MakeRequest
    
    MakeRequest --> CheckError{API<br/>error?}
    CheckError -->|Yes| Retry[Retry with backoff:<br/>- 3 attempts<br/>- Exponential delay]
    CheckError -->|No| SaveCache[Save to cache]
    
    Retry --> RetrySuccess{Success?}
    RetrySuccess -->|Yes| SaveCache
    RetrySuccess -->|No| LogError[Log error]
    
    SaveCache --> ReturnResponse[Return response]
    ReturnCached --> End([Response])
    ReturnResponse --> End
    LogError --> Fail([API Failed])
    
    style Start fill:#90EE90
    style End fill:#90EE90
    style Fail fill:#FFB6C1
    style LoadCache fill:#87CEEB
    style SaveCache fill:#FFE4B5
```

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

**Generated:** 2026-03-13  
**Pipeline Version:** 2.0
