# API Reference

## Core Classes

### GrokClient

API client for Grok with caching support.

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
from src.schemas import EventOutput

result = client.extract_structured(
    "Extract events from: ...",
    EventOutput,
    system_prompt="You are a WWII historian.",
    cache_type="events"
)
```

##### `extract_json(prompt, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default")`
Extract JSON without schema validation.

**Parameters:**
- `prompt` (str) - Extraction prompt
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")

**Returns:** Dict[str, Any]

##### `chat_completion(prompt, system_prompt=None, temperature=0.1, use_cache=True, cache_type="default")`
Get chat completion from Grok API.

**Parameters:**
- `prompt` (str) - User prompt
- `system_prompt` (Optional[str]) - System prompt (default: None)
- `temperature` (float) - Sampling temperature (default: 0.1)
- `use_cache` (bool) - Whether to use cache (default: True)
- `cache_type` (str) - Cache type (default: "default")

**Returns:** str - Response text from API

##### `clear_cache_entry(prompt, cache_type="default", temperature=0.1)`
Remove a single cache entry by prompt.

**Parameters:**
- `prompt` (str) - The prompt used for the original request
- `cache_type` (str) - Cache type (default: "default")
- `temperature` (float) - Temperature used for the original request (default: 0.1)

**Returns:** bool - True if entry was removed

**Example:**
```python
client.clear_cache_entry("Extract events from: ...", "events")
```

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

##### `submit_batch(batch_name="pipeline")`
Submit collected batch requests to xAI Batch API. Only useful when `batch_mode=True`.

**Parameters:**
- `batch_name` (str) - Name for the batch (default: "pipeline")

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
    parsed_file=Path("output/Book/chapter1-parsed.json"),
    grok_client=client,
    output_dir=Path("output/Book")
)
```

**Parameters:**
- `parsed_file` (Path) - Parsed JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Output directory

**Returns:** Path to event file

**Output:** `{output_dir}/chapter*-event.json`

---

### Dates

```python
from src.extraction.dates import extract_dates

dates_file = extract_dates(
    event_file=Path("output/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output/Book")
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Output directory

**Returns:** Path to dates file

**Output:** `{output_dir}/chapter*-dates.json`

---

### Places

```python
from src.extraction.places import extract_places

places_file = extract_places(
    event_file=Path("output/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output/Book")
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Output directory

**Returns:** Path to places file

**Output:** `{output_dir}/chapter*-places.json`

---

### People

```python
from src.extraction.people import extract_people

people_dir = extract_people(
    event_file=Path("output/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output")
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Base output directory

**Returns:** Path to people directory

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
    event_file=Path("output/Book/chapter1-event.json"),
    grok_client=client,
    output_dir=Path("output")
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `grok_client` (GrokClient) - API client
- `output_dir` (Path) - Base output directory

**Returns:** Path to groups directory

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
    event_file=Path("output/Book/chapter1-event.json"),
    output_dir=Path("output/equipment"),
    grok_client=client,
    dates_dir=Path("output/dates"),
    people_dir=Path("output/people")
)
```

**Parameters:**
- `event_file` (Path) - Event JSON file
- `output_dir` (Path) - Equipment output directory
- `grok_client` (GrokClient) - API client
- `dates_dir` (Optional[Path]) - Dates directory for linking
- `people_dir` (Optional[Path]) - People directory for linking

**Returns:** List[Path] - Created equipment files

**Output:** `{output_dir}/{Name}_{EquipmentID}.json`

**Features:**
- Links to EventID and Sub_eventID
- Links to DateID if dates available
- Links to PersonID if person found
- Tracks performance (successes, failures, modifications)
- Experimental feature (disabled by default)

---

## Parser Functions

### parse_chapter

```python
from src.parser import parse_chapter
from src.models import ChapterGroup

documents = parse_chapter(
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
- `meta_file` (Path) - Metadata file (.md or .yaml)

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
- Expands environment variables
- Returns: Dict[str, Any]

**get_paths(config, base_dir)**
- Resolves paths from config
- Makes paths absolute
- Returns: Dict[str, Path]

---

### Logging

```python
from src.utils.logger import setup_logging

logger = setup_logging(
    level="INFO",           # TRACE, DEBUG, INFO, WARN, ERROR, FATAL
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
    paragraphs=[...],       # List[Paragraph]
    images=[...],           # List[Image]
    maps=[...],             # List[Map]
    footnotes=[...],        # List[Footnote]
    page_markers=[...],     # List[PageMarker]
    file_path=Path("..."),  # Optional[Path]
    meta_path=Path("...")   # Optional[Path]
)
```

---

## Schema Models

### Person

```python
from src.schemas import Person, BiographicalProfile, PersonEventMention

person = Person(
    PersonID="01HXYZ...",
    name="Dwight D. Eisenhower",
    biographical_profile=BiographicalProfile(
        full_name="Dwight David Eisenhower",
        birth_date="1890-10-14",
        death_date="1969-03-28",
        nationality="American",
        military_branch="United States Army",
        highest_rank="General of the Army",
        positions_held=["Supreme Commander Allied Forces"],
        awards=[],
        education=[],
        notable_relationships=[]
    ),
    event_mentions=[
        PersonEventMention(
            EventID="01ABC...",
            Sub_eventID="01DEF...",
            position_at_event="Supreme Commander",
            actions_taken=["Approved operation"],
            book="Breakout and Pursuit",
            author="Martin Blumenson",
            series="United States Army in World War II"
        )
    ],
    aliases=["Ike", "General Eisenhower"]
)
```

---

### PeopleGroup

```python
from src.schemas import PeopleGroup, PeopleGroupEventMention

group = PeopleGroup(
    GroupID="01HXYZ...",
    group_name="Wehrmacht",
    group_type="military_unit",
    members=[
        {
            "PersonID": "01ABC...",
            "name": "Erwin Rommel",
            "role": "Field Marshal",
            "date_range": "1940-1944"
        }
    ],
    event_mentions=[
        PeopleGroupEventMention(
            EventID="01ABC...",
            Sub_eventID="01DEF...",
            role_in_event="Defending force",
            actions_taken=["Established defensive positions"],
            book="Breakout and Pursuit",
            author="Martin Blumenson",
            series="United States Army in World War II"
        )
    ]
)
```

---

### Event

```python
from src.schemas import Event, SubEvent

event = Event(
    EventID="01HXYZ...",
    event_name="Operation Overlord",
    event_description="Allied invasion of Normandy",
    start_date="1944-06-06",
    end_date="1944-08-30",
    sub_events=[
        SubEvent(
            Sub_eventID="01ABC...",
            sub_event_name="D-Day Landings",
            sub_event_description="Amphibious assault",
            start_date="1944-06-06",
            end_date="1944-06-06",
            fulltext=SubEventFulltext(
                paragraph_numbers=[1, 2, 3],
                text="Full text of sub-event..."
            )
        )
    ]
)
```

---

## Helper Functions

### ULID Generation

```python
from src.schemas import generate_ulid

ulid = generate_ulid()  # Returns: "01HXYZ..."
```

**Features:**
- Lexicographically sortable
- Timestamp-based
- 26 characters
- URL-safe

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
