# API Reference

## Core Classes

### GrokClient

API client for Grok with caching support.

```python
from src.grok_client import GrokClient
from pathlib import Path

client = GrokClient(
    cache_dir=Path("cache/api"),
    api_key="your-api-key"  # Optional, reads from env
)
```

#### Methods

##### `extract_structured(schema, prompt, temperature=0.1)`
Extract structured data using Pydantic schema.

**Parameters:**
- `schema` (Type[BaseModel]) - Pydantic model class
- `prompt` (str) - Extraction prompt
- `temperature` (float) - Sampling temperature (default: 0.1)

**Returns:** Instance of schema class

**Example:**
```python
from src.schemas import EventOutput

result = client.extract_structured(
    EventOutput,
    "Extract events from: ...",
    temperature=0.1
)
```

##### `extract_json(prompt, temperature=0.1)`
Extract JSON without schema validation.

**Parameters:**
- `prompt` (str) - Extraction prompt
- `temperature` (float) - Sampling temperature

**Returns:** Dict[str, Any]

##### `chat_completion(messages, temperature=0.1)`
Raw chat completion.

**Parameters:**
- `messages` (List[Dict]) - Chat messages
- `temperature` (float) - Sampling temperature

**Returns:** Dict[str, Any] - API response

##### `clear_cache(cache_type=None)`
Clear API cache.

**Parameters:**
- `cache_type` (Optional[str]) - Specific cache to clear (events, dates, places, people, people_groups) or None for all

**Example:**
```python
client.clear_cache("events")  # Clear events cache
client.clear_cache()          # Clear all caches
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
        book_name="BreakoutAndPursuit",
        chapter_name="chapter1",
        content_files=[Path("chapter1a-content.md")],
        meta_file=Path("chapter1-meta.yaml")
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

metadata = parse_metadata(Path("chapter1-meta.yaml"))
```

**Parameters:**
- `meta_file` (Path) - Metadata file (.yaml or .md)

**Returns:** Metadata object

**Fields:**
- `series` (str)
- `book` (str)
- `author` (str)
- `chapter_number` (Optional[str])
- `chapter_title` (Optional[str])
- `license` (str)
- `copyright_date` (Optional[str])
- `source_url` (Optional[str])

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
            book_name="BreakoutAndPursuit",
            chapter_name="chapter1",
            content_files=[...],
            meta_file=Path(...)
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

```python
from src.models import Metadata

metadata = Metadata(
    series="United States Army in World War II",
    book="Breakout and Pursuit",
    author="Martin Blumenson",
    chapter_number="I",
    chapter_title="The Period of Indecision",
    license="Public Domain",
    copyright_date="1961",
    source_url="https://..."
)
```

---

### MarkdownDocument

```python
from src.models import MarkdownDocument, Paragraph

doc = MarkdownDocument(
    metadata=metadata,
    paragraphs=[
        Paragraph(
            paragraph_number=1,
            text="Content...",
            images=[],
            maps=[],
            footnotes=[],
            page_markers=[]
        )
    ],
    chapter_name="chapter1"
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
    result = client.extract_structured(schema, prompt)
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
