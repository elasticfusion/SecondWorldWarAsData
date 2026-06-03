# Code Architecture

## Overview

The codebase is organized into:
- **Main scripts** - Pipeline entry points and utilities
- **src/** - Core library modules
- **Extraction modules** - Entity-specific extraction logic
- **Utilities** - Configuration, logging, caching

## Main Scripts

### Pipeline Scripts

#### `phase1_parse.py`
Discovers and parses markdown content into structured JSON.

**Key functions:**
- Discovers books and chapters in `contentrepository/`
- Parses markdown with absolute paragraph numbering
- Extracts inline entities (images, maps, footnotes, page markers)
- Reads metadata from YAML files
- Outputs to `output/{Book}/chapter*-parsed.json`
- **Source hash skip:** Computes content hash and skips writing/uploading if unchanged
- **Lock scoping:** Only clears its own Phase 1 lock (not Phase 2/3 locks)
- **Full-sync scoped by `BOOK_NAME`** env var in AWS mode

**Usage:**
```bash
python3 phase1_parse.py
```

#### `phase2_extract.py`
Main extraction pipeline using Grok AI.

**Workflow (5 stages):**
1. Parallel core extraction — events + dates + places + people_groups + people (batched, concurrent)
2. Retry missing events — per-chapter cache clear and re-extract
3. Optional entity extraction — weather, equipment, logistics, casualties, images, supplemental (sequential per event file, batched per chapter)
4. Maps — source maps + external maps via OpenSERP
5. Analysis — duplicate people report + related groups report

**Key features:**
- Parallel chapter processing via `batch_parallel.py`
- Batched API calls (multiple entities per request)
- Targeted per-entry cache clearing on failure
- Caching of all API responses
- Incremental extraction (people/groups accumulate)
- Automatic report generation

**Usage:**
```bash
python3 phase2_extract.py [--log-level LEVEL] [--batch]
```

`--batch` uses xAI Batch API for 50% cost reduction (async processing via `src/utils/batch_api.py`).

#### `phase3_enrich_data.py`
Enriches extracted entities with external data.

**What it enriches:**
- People — biographical data from Wikipedia/Grokipedia (birth/death dates, service history, awards)
- Groups — additional organizational context and hierarchy
- Places — enhanced geographic data and historical context
- Bibliography — expanded citation metadata and source verification

**Options:**
- `--max-items N` — limit items per entity type
- `--people-only` — skip non-people enrichment
- `--no-references` — skip reference following (faster)

**Usage:**
```bash
python3 phase3_enrich_data.py [--people-only] [--max-items 50]
```

#### `phase2_retry.py` / `phase3_retry.py`
Automatic retry wrappers for Phase 2 and Phase 3.

**Features:**
- Runs the underlying phase script multiple times
- Counts remaining work after each attempt
- Stops early when all files are processed
- Default: 3 attempts

**Usage:**
```bash
python3 phase2_retry.py [--max-attempts 5]
python3 phase3_retry.py [--max-attempts 5]
```

### Utility Scripts (`scripts/`)

#### `find_duplicate_people.py`
Detects potential duplicate people using 8 heuristics.

**Detection methods:**
1. Name similarity (70%+ threshold)
2. ASCII/Unicode variants (Dönitz ↔ Donitz)
3. Substring matching (Eisenhower ↔ D. Eisenhower)
4. Shared biographical data
5. Shared positions
6. Shared event mentions
7. Name component overlap
8. Exclusion list checking

**Output:** `output/people/duplicate_report.json`

**Usage:**
```bash
python3 scripts/find_duplicate_people.py
```

#### `merge_duplicate_people.py`
Interactive duplicate merging with user confirmation.

**Features:**
- Interactive review (y/n/skip/exclude)
- Merges event mentions and biographical data
- Updates index and deletes duplicate files
- Exclusion list for false positives

**Usage:**
```bash
python3 scripts/merge_duplicate_people.py [--auto-confirm]
```

**Interactive options:**
- `y` - Merge this group
- `n` - Skip this group
- `skip` - Skip this group (same as n)
- `exclude` - Mark as false positive (never suggest again)

### People Groups Scripts

#### `find_related_groups.py`
Detects related people groups using 8 heuristics.

**Detection methods:**
1. Name similarity
2. Substring matching
3. Shared members
4. Hierarchical relationships
5. Temporal subdivisions
6. Alias variations
7. Type consistency
8. Exclusion list checking

**Output:** `output/people_groups/related_groups_report.json`

**Usage:**
```bash
python3 scripts/find_related_groups.py
```

#### `suggest_group_aliases.py`
Grok-powered alias suggestions for people groups.

**Features:**
- AI-powered alias detection
- Interactive review (y/n/skip/edit)
- Updates `people_group_aliases.yaml`
- Handles hierarchies and temporal subdivisions

**Usage:**
```bash
python3 scripts/suggest_group_aliases.py
```

**Interactive options:**
- `y` - Accept suggestion
- `n` - Reject suggestion
- `skip` - Skip this suggestion
- `edit` - Manually specify canonical group

#### `consolidate_people_groups.py`
Applies aliases and merges people groups.

**Features:**
- Reads `people_group_aliases.yaml`
- Merges groups based on aliases
- Preserves temporal subdivisions
- Updates index and deletes duplicates

**Usage:**
```bash
python3 scripts/consolidate_people_groups.py
```

### Metadata Scripts

#### `generate_missing_metadata.py`
Creates YAML metadata files with smart defaults.

**Features:**
- Detects missing metadata files
- Extracts book/series from directory structure
- Creates templates with placeholders
- Skips existing files

**Usage:**
```bash
python3 scripts/generate_missing_metadata.py
```

#### `complete_metadata_with_grok.py`
Grok-powered metadata extraction from content.

**Features:**
- Extracts chapter_number and chapter_title
- Reads content markdown files
- Updates YAML metadata files
- Handles Roman numerals and special formats

**Usage:**
```bash
python3 scripts/complete_metadata_with_grok.py
```

#### `standardize_metadata.py`
Converts old `.md` metadata to YAML format.

**Features:**
- Migrates from markdown to YAML
- Preserves all metadata fields
- Extracts copyright dates
- Creates backup of old files

**Usage:**
```bash
python3 scripts/standardize_metadata.py
```

### Utility Scripts

#### `extract_url.py`
Extracts content from web URLs.

**Features:**
- Fetches HTML content
- Converts to markdown
- Splits into chapters
- Saves to contentrepository

**Usage:**
```bash
python3 scripts/extract_url.py <url>
```

#### `pdf_to_markdown.py`
Converts PDF files to markdown format.

**Features:**
- Extracts text from PDF pages
- Creates proper directory structure
- Generates metadata template
- Adds page markers

**Usage:**
```bash
python3 scripts/pdf_to_markdown.py document.pdf "BookName"
```

**Requirements:**
```bash
pip install pymupdf
```

#### `review_cache.py`
Inspects API cache contents.

**Features:**
- Lists cached API calls
- Shows cache statistics
- Displays sample responses

**Usage:**
```bash
python3 scripts/review_cache.py
```

#### `validate_output.py`
Validates all output JSON files across every entity type.

**Checks per file:** required fields, correct types, valid ULID format on ID fields, valid ULIDs in event_mentions and sub-events.

**Entity types:** event, notes-event, date, place, person, people_group, equipment, casualty, weather, logistics, map, bibliography, supplemental.

**Usage:**
```bash
python3 scripts/validate_output.py
```

## Core Library (`src/`)

### Models (`src/models.py`)

Data models for parsed content:

```python
class Metadata:
    series: str
    book: str
    author: str
    chapter_number: Optional[str]
    chapter_title: Optional[str]
    license: str
    copyright_date: Optional[str]
    source_url: Optional[str]

class Paragraph:
    paragraph_number: int
    text: str
    images: List[Image]
    maps: List[Map]
    footnotes: List[Footnote]
    page_markers: List[PageMarker]

class MarkdownDocument:
    metadata: Metadata
    paragraphs: List[Paragraph]
    chapter_name: str
```

### Schemas (`src/schemas.py`)

Pydantic models for structured outputs:

**Event schemas:**
- `EventOutput` - Top-level event extraction
- `Event` - Individual event
- `SubEvent` - Sub-event with fulltext

**Entity schemas:**
- `DateOutput` / `DateMention` - Temporal entities
- `PlaceOutput` / `PlaceMention` - Geographic entities
- `PeopleOutput` / `Person` - People with biographical profiles
- `PeopleGroupOutput` / `PeopleGroup` - Organizations/units

**Supporting schemas:**
- `BiographicalProfile` - Person details
- `PersonEventMention` - Person-event links
- `PeopleGroupEventMention` - Group-event links

### Grok Client (`src/grok_client.py`)

API client with caching:

```python
class GrokClient:
    def extract_structured(schema, prompt, temperature=0.1)
        # Structured output with Pydantic schema
    
    def extract_json(prompt, temperature=0.1)
        # JSON extraction
    
    def chat_completion(messages, temperature=0.1)
        # Raw chat completion
    
    def clear_cache(cache_type=None)
        # Clear API cache
```

**Cache structure:**
- `cache/api/events/` - Event extractions
- `cache/api/dates/` - Date extractions
- `cache/api/places/` - Place extractions
- `cache/api/people/` - People extractions
- `cache/api/people_groups/` - Group extractions

### JSON Schemas (`src/json_schemas.py`)

JSON Schema definitions for validation of extracted entity files.

### Types (`src/types.py`)

Shared type definitions and type aliases used across the codebase.

### URL Extractor (`src/url_extractor.py`)

Extracts and normalizes URLs from text content.

### Parser (`src/parser.py`)

Markdown parsing with entity extraction:

```python
def parse_chapter(chapter_group: ChapterGroup) -> List[MarkdownDocument]
    # Main parsing entry point

def parse_content_file(content_file: Path, metadata: Metadata) -> MarkdownDocument
    # Parse single content file

def parse_metadata(meta_file: Path) -> Metadata
    # Read YAML metadata (falls back to .md)

def split_into_paragraphs(text: str) -> List[str]
    # Split on double newlines

def extract_footnotes(text: str) -> List[Tuple[int, str]]
    # Extract [^1]: footnotes

def extract_maps(text: str) -> List[Tuple[str, str]]
    # Extract [MAP: ...] markers

def extract_images(text: str) -> List[Tuple[str, str, str, str]]
    # Extract ![alt](url "caption") images

def extract_page_markers(text: str) -> List[Tuple[int, int, str]]
    # Extract [p. 123] markers
```

### Discovery (`src/discovery.py`)

Content structure discovery:

```python
def discover_content_structure(content_root: Path) -> Dict[str, List[ChapterGroup]]
    # Discovers books and chapters
    # Returns: {book_name: [ChapterGroup, ...]}
```

## Extraction Modules (`src/extraction/`)

### Events (`src/extraction/events.py`)

```python
def extract_events(parsed_file: Path, grok_client: GrokClient, output_dir: Path) -> Path
    # Extracts hierarchical events from parsed JSON
    # Returns: event_file path

def create_event_prompt(parsed_data: Dict) -> str
    # Creates extraction prompt from parsed content
```

### Supplemental (`src/extraction/supplemental.py`)

Split architecture: classifies each endnote/footnote as `document_reference`, `factual_content`, or `ambiguous`, then routes accordingly.

```python
def extract_supplemental(event_file: Path, grok_client: GrokClient, output_dir: Path, ...) -> Optional[Path]
    # Classifies and routes supplemental material
    # document_reference → output/bibliography/ (via bibliography.py)
    # factual_content → output/{Book}/{chapter}-notes-event.json
    # ambiguous → output/bibliography/review_queue.json
```

**Output paths:**
- `output/bibliography/{title_slug}_{ULID}.json` — deduplicated document references
- `output/{Book}/{chapter}-notes-event.json` — factual content as event-like JSON
- `output/bibliography/review_queue.json` — ambiguous items for human review

### Bibliography (`src/extraction/bibliography.py`)

Deduplicated document storage with fuzzy title matching.

```python
def store_bibliography_entry(material: Dict, bib_dir: Path) -> str
    # Merge-or-create bibliography entry
    # Returns: BibliographyID
```

**Key features:**
- One JSON file per unique document
- Fuzzy title matching for deduplication
- Cross-book mention tracking
- `alt_title` for expanded abbreviations

### Dates (`src/extraction/dates.py`)

```python
def extract_dates(event_file: Path, grok_client: GrokClient, output_dir: Path) -> Path
    # Extracts temporal entities from events
    # Returns: dates_file path

def create_date_prompt(sub_event: Dict, event_id: str, event_name: str) -> str
    # Creates extraction prompt for sub-event
```

### Places (`src/extraction/places.py`)

```python
def extract_places(event_file: Path, grok_client: GrokClient, output_dir: Path) -> Path
    # Extracts geographic entities with coordinates
    # Returns: places_file path

def create_place_prompt(sub_event: Dict, event_id: str, event_name: str) -> str
    # Creates extraction prompt for sub-event
```

### People (`src/extraction/people.py`)

File-per-person architecture:

```python
def extract_people(event_file: Path, grok_client: GrokClient, output_dir: Path) -> Path
    # Extracts people to individual files
    # Returns: people_dir path

def create_people_prompt(sub_event: Dict, event_id: str, event_name: str) -> str
    # Creates extraction prompt for sub-event
```

**Key features:**
- Individual JSON files per person
- Central index for lookups
- Automatic deduplication by PersonID
- Merges event mentions across books
- Preserves biographical data

**File structure:**
```
output/people/
├── index.json                           # {name: filename}
├── Dwight_D_Eisenhower_01ABC.json      # Individual person
├── George_S_Patton_01DEF.json
└── duplicate_report.json                # Auto-generated
```

### People Groups (`src/extraction/people_groups.py`)

File-per-group architecture:

```python
def extract_people_groups(event_file: Path, grok_client: GrokClient, output_dir: Path) -> Path
    # Extracts groups to individual files
    # Returns: groups_dir path
```

**Key features:**
- Individual JSON files per group
- Central index for lookups
- Members field linking to people
- Automatic deduplication by GroupID
- Merges event mentions across books

**File structure:**
```
output/people_groups/
├── index.json                           # {name: filename}
├── Wehrmacht_01ABC.json                 # Individual group
├── Allied_Forces_01DEF.json
└── related_groups_report.json           # Auto-generated
```

### Images (`src/extraction/images.py`)

Extracts and processes image references from parsed content.

### Combined Map Search (`src/extraction/combined_map_search.py`)

Unified map search across multiple sources (source maps and external maps via OpenSERP).

### Supplemental Advanced (`src/extraction/supplemental_advanced.py`)

Advanced supplemental material extraction with enhanced classification and routing.

### Copyright Calculator (`src/extraction/copyright_calculator.py`)

Calculates copyright status and public domain eligibility for source documents.

## Utilities (`src/utils/`)

### Configuration (`src/utils/config.py`)

```python
def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]
    # Loads config.yaml

def get_paths(config: Dict, base_dir: Optional[Path] = None) -> Dict[str, Path]
    # Resolves paths from config
```

### Logging (`src/utils/logger.py`)

```python
def setup_logging(level: str = "INFO", log_file: Optional[str] = None, console: bool = True)
    # Configures logging with custom TRACE level
```

### Heartbeat (`src/utils/heartbeat.py`)

Progress monitoring that warns if no pipeline activity is detected for a configurable interval. Logs per-chapter heartbeat progress counter during Phase 2 and Phase 3.

### Batch API (`src/utils/batch_api.py`)

Client for xAI Batch API supporting async batch submission and result retrieval for 50% cost reduction.

### Job Queue (`src/utils/job_queue.py`)

DynamoDB-backed job queue for tracking batch submissions. Manages `batch_job#{batch_id}` entries with status tracking (pending/complete/failed/retrieved) and 30-day TTL auto-cleanup.

### HTTP Pool (`src/utils/http_pool.py`)

Connection pooling and retry logic for HTTP requests. Maintains persistent sessions (connection pool fix — sessions are not destroyed per API call).

### File Lock (`src/utils/file_lock.py`)

File-based locking for safe concurrent access to shared output files. Uses **dual-layer locking**: per-file `threading.Lock` (prevents in-process races from parallel chapter processing) + `fcntl.flock` (prevents cross-process races). The `locked_json()` context manager holds both locks across the full read-modify-write cycle. `write_json_with_lock()` provides atomic writes via temp file + `os.replace()` for crash safety.

### Schema Registry (`src/utils/schema_registry.py`)

Central registry of Pydantic schemas for entity validation and serialization.

### Validation Reports (`src/utils/validation_reports.py`)

Generates validation reports and dashboards for extracted data quality.

## Configuration Files

### `config.yaml`
Main configuration:
```yaml
paths:
  content_root: contentrepository
  output_root: output
  cache_root: cache

api:
  grok:
    model: "grok-beta"

logging:
  level: INFO
  console: true
  file: null
```

### `people_group_aliases.yaml`
Group alias definitions:
```yaml
aliases:
  canonical_name:
    - alias1
    - alias2

hierarchies:
  parent:
    - child1
    - child2

merge_rules:
  - groups: [group1, group2]
    canonical: group1

exclusions:
  - [group1, group2]  # Never merge

temporal_context:
  France:
    subdivisions:
      - Vichy France
      - Free France
```

### `output/people/not_duplicates.json`
Exclusion list for false positives:
```json
[
  ["PersonID1", "PersonID2"],
  ["PersonID3", "PersonID4"]
]
```

## Data Flow

```
contentrepository/
  └── {Book}/chapter*/
      ├── chapter*-meta.yaml      → Phase 1 → output/{Book}/
      └── chapter*-content.md                  ├── chapter*-parsed.json
                                               │
                                    Phase 2 ↓  │
                                               ├── chapter*-event.json
                                               ├── chapter*-notes-event.json  (factual endnotes)
                                               ├── chapter*-dates.json
                                               ├── chapter*-places.json
                                               │
                                               └── people/
                                                   ├── {Name}_{ID}.json
                                                   └── index.json
                                               
                                               └── people_groups/
                                                   ├── {Group}_{ID}.json
                                                   └── index.json

                                               └── bibliography/
                                                   ├── {title_slug}_{ID}.json
                                                   ├── index.json
                                                   └── review_queue.json
```

## Key Design Patterns

### File-per-Entity
People and groups use individual files for:
- Scalability (thousands of entities)
- Incremental updates
- Easy merging across books
- Simple deduplication

### Caching Strategy
All API responses cached by:
- Prompt hash
- Temperature
- Cache type (events, dates, places, etc.)

### Skip Logic
Timestamp-based processing:
- Events/dates/places: Skip if output exists
- People/groups: Skip if newer than event file

### ULID Identifiers
All entities use ULIDs for:
- Unique identification
- Cross-referencing
- Sortable by creation time

### Structured Outputs
Pydantic schemas ensure:
- Type safety
- Validation
- Consistent structure
- Easy serialization
