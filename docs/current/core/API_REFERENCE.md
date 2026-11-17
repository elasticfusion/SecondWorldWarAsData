# API Reference

**Last Updated:** 2026-11-16 (signatures checked against `src/` on branch `refactor/pr1-dedup-into-src`)

## Core Classes

### GrokClient

API client for Grok with caching support (`src/grok_client.py`). The pipeline model is `GROK_MODEL` env, else `api.grok.model` (`grok-4.3`), resolved in `src/utils/grok_models.py`. In AWS mode (`aws.enabled`) `cache_dir` is ignored in favour of the DynamoDB cache table; a `CacheBackend` can also be passed.

```python
from src.grok_client import GrokClient
from pathlib import Path

client = GrokClient(
    cache_dir=Path("cache/api"),
    api_key="your-api-key",  # Optional, reads from env
    batch_mode=False          # Optional, collect requests for batch submission
)
```

#### Methods

##### `extract_structured(prompt, schema, system_prompt=None, use_cache=True, cache_type="default")`
Extract structured data using Pydantic schema.

**Parameters:**
- `prompt` (str) - Extraction prompt
- `schema` (Type[BaseModel]) - Pydantic model class defining the output structure
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type, e.g. "events", "dates", "places" (default: "default")

**Returns:** Instance of schema class

**Example:**
```python
from pydantic import BaseModel

class MyOutput(BaseModel):   # the live Pydantic models sit next to their extractor, e.g. people.PeopleOutput
    summary: str

result = client.extract_structured(
    "Extract events from: ...",
    MyOutput,
    system_prompt="You are a WWII historian.",
    cache_type="events"
)
```

##### `extract_json(prompt, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default", cache_key=None, book=None)`
Extract JSON without schema validation.

**Parameters:**
- `prompt` (str) - Extraction prompt
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")
- `cache_key` (Optional[str]) - Explicit cache key (default: hash of system prompt + prompt + temperature + model)
- `book` (Optional[str]) - Book slug. For a book-bound `cache_type` the request carries the BookID and its own RequestID, and an answer that does not echo both is never trusted (`src/utils/request_identity`); the cache is scoped `books/<book>/<kind>`.

**Returns:** Dict[str, Any]

##### `chat_completion(prompt, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default", cache_key=None)`
Get chat completion from Grok API.

**Parameters:**
- `prompt` (str) - User prompt
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")

**Returns:** str - Response text from API

##### `clear_cache_entry(prompt, cache_type="default", temperature=0.1, system_prompt=None, book=None)`
Remove a single cache entry by prompt.

**Parameters:**
- `prompt` (str) - The prompt used for the original request
- `cache_type` (str) - Cache type (default: "default")
- `temperature` (float) - Temperature used for the original request (default: 0.1)
- `system_prompt`, `book` - Must match the original request (they are part of the key)

**Returns:** bool - True if entry was removed

**Example:**
```python
client.clear_cache_entry("Extract events from: ...", "events")
```

##### Other methods
`cache_key_for(prompt, system_prompt=None, cache_type="default", temperature=0.1, book=None)` returns the cache key a request would use; `store_json_answer(prompt, system_prompt, cache_type, answer, temperature=0.1, book=None, cache_key=None)` writes a retrieved batch answer into the cache.

##### `extract_json_with_image_base64(prompt, image_base64, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default")`
Get JSON response from Grok API with base64 image input (vision).

**Parameters:**
- `prompt` (str) - User prompt
- `image_base64` (str) - Base64-encoded image data
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")

**Returns:** Dict[str, Any] - Parsed JSON response

##### `extract_json_with_image(prompt, image_url, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default", image_timeout=30)`
Get JSON response from Grok API with image URL input (vision). Downloads the image and sends as base64.

**Parameters:**
- `prompt` (str) - User prompt
- `image_url` (str) - URL of image to analyze
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")
- `image_timeout` (int) - Timeout for image download in seconds (default: 30)

**Returns:** Dict[str, Any] - Parsed JSON response

##### `submit_batch(batch_name="pipeline", max_wait_minutes=None)`
Submit collected batch requests to xAI Batch API. Only useful when `batch_mode=True`. In Phase 2 this is driven by the batch-round loop (`src/utils/batch_rounds.py`), which waits up to `batch.in_task_wait_minutes` and otherwise hands the batch to the batch-poller Lambda.

**Parameters:**
- `batch_name` (str) - Name for the batch (default: "pipeline")
- `max_wait_minutes` (Optional[float]) - How long to wait for an answer (default: `batch.in_task_wait_minutes`)

**Returns:** Optional[str] - Batch ID, or None if no requests to submit

**Example:**
```python
client = GrokClient(cache_dir, batch_mode=True)
# ... run extraction (requests are queued, not sent) ...
batch_id = client.submit_batch("my_batch")
```

---

## Extraction Functions

### Events

```python
from src.extraction.events import extract_events
from pathlib import Path

event_file = extract_events(
    parsed_file=Path("output/content/Book/chapter1-parsed.json"),
    grok_client=client,
    output_dir=Path("output/content/Book"),
    max_retries=3,
)
```

**Parameters:**
- `parsed_file` (Path) - Parsed JSON file (`output/content/<book>/chapter*-parsed.json`)
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Output directory
- `max_retries` (int) - Default 3

**Returns:** Optional[Path] - path to the event file, `None` on failure

**Output:** `{output_dir}/chapter*-event.json`. One request per chapter yields an Event with Sub-events; orphan paragraphs are claimed by the nearest earlier sub-event (`events._claim_orphan_paragraphs`). EventIDs are deterministic (`deterministic_ids.event_id`).

---

### Dates

```python
from src.extraction.dates import extract_dates

dates_file = extract_dates(
    event_file=Path("output/content/Book/chapter1-event.json"),
    grok_client=client,
    dates_dir=Path("output/dates"),
    parsed_file=Path("output/content/Book/chapter1-parsed.json"),
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `dates_dir` (Path) - Entity directory (one file per date plus `index.json`)
- `parsed_file` (Optional[Path]) - The parsed chapter. Discovery runs over fixed 3-paragraph windows of this raw text (`src/extraction/windows.py`), never the events' summaries; each item is attributed to the sub-event holding its quote.
- `max_retries` (int) - Default 3

**Returns:** Optional[Path]

**Output:** `{dates_dir}/<date file>.json` and `{dates_dir}/index.json`

---

### Places

```python
from src.extraction.places import extract_places

places_file = extract_places(
    event_file=Path("output/content/Book/chapter1-event.json"),
    grok_client=client,
    places_dir=Path("output/places"),
    parsed_file=Path("output/content/Book/chapter1-parsed.json"),
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `places_dir` (Path) - Entity directory (one file per place plus `index.json`)
- `parsed_file` (Optional[Path]) - The parsed chapter (windowed discovery, as for dates)
- `max_retries` (int) - Default 3

**Returns:** Optional[Path]

**Output:** `{places_dir}/<place file>.json` and `{places_dir}/index.json`

---

### People

```python
from src.extraction.people import extract_people

people_dir = extract_people(
    event_file=Path("output/content/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output"),
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Base output directory
- `max_retries` (int) - Default 3

**Returns:** Optional[Path] - the people directory

**Output:** 
- `{output_dir}/people/{Name}_{PersonID}.json` - Individual person files
- `{output_dir}/people/index.json` - Name → filename mapping

**Features:**
- Automatic deduplication by PersonID
- Merges event mentions for existing people
- Updates biographical profiles
- Maintains central index

---

### People Groups

```python
from src.extraction.people_groups import extract_people_groups

groups_dir = extract_people_groups(
    event_file=Path("output/content/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output"),
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Base output directory

**Returns:** Optional[Path] - the groups directory

**Output:**
- `{output_dir}/people_groups/{Group}_{GroupID}.json` - Individual group files
- `{output_dir}/people_groups/index.json` - Name → filename mapping

**Features:**
- Automatic deduplication by GroupID
- Merges event mentions for existing groups
- Links members to people
- Maintains central index

---

### Equipment

```python
from src.extraction.equipment import extract_equipment_from_event

equipment_files = extract_equipment_from_event(
    event_file=Path("output/content/Book/chapter1-event.json"),
    output_dir=Path("output/equipment"),
    grok_client=client,
    output_root=Path("output"),
    enable_enrichment=False,
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `output_dir` (Path) - Equipment output directory
- `grok_client` (GrokClient) - API client
- `output_root` (Optional[Path]) - Output root, used to find the dates/people directories for linking
- `max_retries` (int) - Default 3
- `enable_enrichment` (bool) - Wikipedia/Grokipedia enrichment (default False; `equipment.enable_enrichment`)

**Returns:** List[Path] - Created equipment files

**Output:** `{output_dir}/{Name}_{EquipmentID}.json`

**Features:**
- Links to EventID and Sub_eventID
- Links to DateID if dates available
- Links to PersonID if person found
- Tracks performance (successes, failures, modifications)
- Broad discovery plus a refine pass; lineage fields `variant_of` / `distinguishing_features` (schema 2.30)

---

## Parser Functions

### parse_chapter

```python
from src.parser import parse_chapter
from src.models import ChapterGroup

documents = parse_chapter(   # also accepts apply_structure_repair=False (keyword-only)
    ChapterGroup(
        book="BreakoutAndPursuit",
        chapter_number=1,
        meta_file=Path("chapter1-meta.md"),
        content_files={"chapter1a": Path("chapter1a-content.md")}
    )
)
```

**Parameters:**
- `chapter_group` (ChapterGroup) - Chapter metadata

**Returns:** List[MarkdownDocument]

---

### parse_metadata

```python
from src.parser import parse_metadata
from pathlib import Path

metadata = parse_metadata(Path("chapter1-meta.md"))
```

**Parameters:**
- `meta_file` (Path) - Metadata file (`-meta.md` or the `chapterN-meta.yaml` that OCR promotion writes)

**Returns:** Metadata object

**Fields:**
- `series` (str)
- `book` (str)
- `author` (str)
- `chapter_title` (str)
- `license` (str)
- `copyright_date` (str)
- `source_url` (str)

---

## Discovery Functions

### discover_content_structure

```python
from src.discovery import discover_content_structure
from pathlib import Path

structure = discover_content_structure(Path("contentrepository"))
```

**Parameters:**
- `content_root` (Path) - Root directory

**Returns:** Dict[str, List[ChapterGroup]]

**Example output:**
```python
{
    "BreakoutAndPursuit": [
        ChapterGroup(
            book="BreakoutAndPursuit",
            chapter_number=1,
            meta_file=Path(...),
            content_files={"chapter1a": Path(...), "chapter1b": Path(...)}
        ),
        ...
    ],
    "Cross-Channel-Attack": [...]
}
```

---

## Utility Functions

### Configuration

```python
from src.utils.config import load_config, get_paths
from pathlib import Path

config = load_config(Path("config.yaml"))
paths = get_paths(config, base_dir=Path.cwd())
```

**load_config(config_path)**
- Loads YAML configuration
- Applies env overrides (`NARA_API_KEY` -> `api.nara_api_key`) and validates (e.g. `api.calls_per_minute` must be 1-200)
- Returns: Dict[str, Any]

**get_paths(config, base_dir)**
- Resolves paths from config
- Resolves each `paths.*` entry against `base_dir`
- Returns: Dict[str, Path]

---

### Logging

```python
from src.utils.logger import setup_logging

logger = setup_logging(
    level="INFO",           # TRACE, DEBUG, INFO, WARN/WARNING, ERROR, FATAL/CRITICAL
    log_file="app.log",     # Optional file output
    console=True            # Console output
)

logger.trace("Detailed trace message")
logger.debug("Debug message")
logger.info("Info message")
```

**Custom TRACE level:**
- Level 5 (below DEBUG)
- Use for very detailed logging

---

## Data Models

### Metadata

Standalone dataclass for chapter metadata parsed from `-meta.md` files.

```python
from src.models import Metadata

metadata = Metadata(
    series="United States Army in World War II",
    book="Breakout and Pursuit",
    author="Martin Blumenson",
    chapter_title="The Period of Indecision",
    license="Public Domain",
    copyright_date="1961",
    source_url="https://..."
)
```

---

### MarkdownDocument

```python
from src.models import MarkdownDocument

doc = MarkdownDocument(
    book="BreakoutAndPursuit",
    chapter_number=1,
    chapter_title="The Period of Indecision",
    section_id="chapter1a",
    author="Martin Blumenson",
    series="United States Army in World War II",
    license="Public Domain",
    source_url="https://...",
    paragraphs=[...],       # List[Paragraph]
    images=[...],           # List[Image]
    maps=[...],             # List[Map]
    footnotes=[...],        # List[Footnote]
    page_markers=[...],     # List[PageMarker]
    table_hints=[...],      # List[dict], from opt-in markdown-structure repair
    file_path=Path("..."),  # Optional[Path]
    meta_path=Path("...")   # Optional[Path]
)
```

---

## Schema Models

`src/schemas/` is a package, so `from src.schemas import Person, Event, PeopleGroup, generate_ulid` **no longer works** (it resolves to `src/schemas/__init__.py`, which has none of them; the old Pydantic module `src/schemas.py` is shadowed and unused). The package provides:

- `ENTITY_SCHEMA_VERSIONS` / `entity_version("<entity>")` - each entity is versioned independently (e.g. places 2.30, people 2.27, events 2.25).
- One `src/schemas/<entity>_output.py` per entity with its JSON Schema (`events_output`, `people_output`, `places_output`, `groups_output`, ...).
- `schema_contract.py` and `entity_registry.py` - the contract checks and the entity registry used by validation.

The Pydantic models used to validate Grok answers live next to their extractor (e.g. `src/extraction/people.py: PeopleOutput`). See [SCHEMA_REFERENCE.md](../SCHEMA_REFERENCE.md) for field definitions.

---

## Helper Functions

### Identifiers

```python
from src.utils.deterministic_ids import event_id, sub_event_id, book_id

eid = event_id("Breakout_and_Pursuit", "chapter1")            # deterministic ULID for one source section
sid = sub_event_id(eid, para_start=12, para_end=30)          # stable within its parent event
bid = book_id("Breakout_and_Pursuit")                        # from the immutable slug
```

EventID, Sub-eventID and BookID are ULID-format strings derived from a sha256 of a key (not random, not timestamped), so re-extracting the same source yields the same ids. Entity ids (PersonID, PlaceID, ...) that the pipeline creates itself use `ulid.new()` from the `ulid-py` package. See [ULID_IMPLEMENTATION.md](ULID_IMPLEMENTATION.md).

---

## Error Handling

### GrokAPIError

```python
from src.grok_client import GrokAPIError

try:
    result = client.extract_structured(prompt, schema)
except GrokAPIError as e:
    print(f"API error: {e}")
```

Raised when:
- API request fails
- Invalid response format
- Rate limiting
- Authentication errors

---

## Best Practices

### Caching
Always use the same `GrokClient` instance to benefit from caching:

```python
# Good
client = GrokClient(cache_dir)
for file in files:
    extract_events(file, client, output_dir)

# Bad - creates new cache each time
for file in files:
    client = GrokClient(cache_dir)
    extract_events(file, client, output_dir)
```

### Error Recovery
Extraction functions are idempotent - safe to re-run:

```python
try:
    extract_events(parsed_file, client, output_dir)
except Exception as e:
    logger.error(f"Failed: {e}")
    # Re-run is safe - will use cache
    extract_events(parsed_file, client, output_dir)
```

### Memory Management
For large datasets, process in batches:

```python
for batch in batches(parsed_files, size=10):
    for file in batch:
        extract_events(file, client, output_dir)
    # Cache is written to disk, memory freed
```
