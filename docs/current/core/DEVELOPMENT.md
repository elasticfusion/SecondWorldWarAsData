# Development Guide

## Setup

### Prerequisites
- Python 3.13+
- Grok API key
- ~2GB disk space for cache
- Chrome or Chromium (for PDF conversion and web scraping)
- Go 1.21+ (for building OpenSERP search tool)

### Installation

```bash
# Clone repository
git clone <repo-url>
cd SecondWorkldWarasData

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install project in editable mode (enables imports without sys.path hacks)
pip install -e .
```

### Configuration

Create `config.yaml`:

```yaml
paths:
  content_root: contentrepository
  output_root: output
  cache_root: cache

api:
  grok:
    api_key: ${GROK_API_KEY}  # Or explicit key
    model: grok-beta

logging:
  level: INFO
  console: true
  file: "logs/pipeline.log"
```

Set environment variable:
```bash
export GROK_API_KEY="your-api-key"
```

Alternatively, create a `.env` file (auto-loaded by the pipeline via `python-dotenv`):
```bash
cp .env.example .env
# Edit .env with your API key
```

---

## Development Workflow

### 1. Add New Content

Place markdown files in `contentrepository/`:

```
contentrepository/
└── BookName/
    └── chapter1/
        ├── chapter1-meta.yaml
        └── chapter1-content.md
```

### 2. Run Pipeline

```bash
# Parse markdown
python3 phase1_parse.py

# Extract entities
python3 phase2_extract.py
```

### 3. Review Results

```bash
# Check for duplicates
python3 scripts/find_duplicate_people.py

# Merge duplicates
python3 scripts/merge_duplicate_people.py

# Find related groups
python3 scripts/find_related_groups.py

# Suggest aliases
python3 scripts/suggest_group_aliases.py

# Consolidate groups
python3 scripts/consolidate_people_groups.py
```

---

## Code Quality

### Quality Assurance Tools

All code must pass:

```bash
# Linting (target: 10/10)
python3 -m pylint script.py --disable=C0301,C0103,R0913,R0914,R0915

# Type checking
python3 -m mypy script.py --ignore-missing-imports

# Security scanning
python3 -m bandit -r script.py

# Complexity analysis
python3 -m radon cc script.py -a

# Code formatting
python3 -m black script.py
```

### Standards

- **Pylint**: 9.9-10/10 (disable only specific warnings)
- **Mypy**: No errors
- **Bandit**: No HIGH/CRITICAL issues
- **Radon**: Average complexity A-B
- **Black**: Formatted

### Common Pylint Disables

```python
# Line too long (handled by black)
# --disable=C0301

# Variable name doesn't conform (for IDs, APIs)
# --disable=C0103

# Too many arguments
# --disable=R0913

# Too many local variables
# --disable=R0914

# Too many statements
# --disable=R0915
```

---

## Adding New Extraction Types

### 1. Create Schema

In `src/schemas.py`:

```python
class NewEntityOutput(BaseModel):
    """Output schema for new entity extraction."""
    entities: List[NewEntity]
    
    class Config:
        extra = "forbid"

class NewEntity(BaseModel):
    """Individual entity."""
    EntityID: str
    name: str
    # ... other fields
    
    class Config:
        extra = "forbid"
```

### 2. Create Extraction Module

In `src/extraction/new_entity.py`:

```python
"""New entity extraction."""

import json
from pathlib import Path
from typing import Dict, Any
from src.grok_client import GrokClient
from src.schemas import NewEntityOutput

def extract_new_entity(
    event_file: Path,
    grok_client: GrokClient,
    output_dir: Path
) -> Path:
    """Extract new entities from events."""
    
    # Load event data
    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)
    
    # Create prompt
    prompt = create_prompt(event_data)
    
    # Extract with Grok
    result = grok_client.extract_structured(
        NewEntityOutput,
        prompt,
        temperature=0.1
    )
    
    # Save output
    output_file = output_dir / f"{event_file.stem.replace('-event', '')}-newentity.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)
    
    return output_file

def create_prompt(event_data: Dict[str, Any]) -> str:
    """Create extraction prompt."""
    return f"""Extract new entities from:
    
{json.dumps(event_data, indent=2)}

Instructions:
- ...
"""
```

### 3. Add to Pipeline

In `phase2_extract.py`:

```python
from src.extraction.new_entity import extract_new_entity

# In main():
logger.info("Extracting new entities...")
extract_new_entity(event_file, grok_client, output_dir)
```

### 4. Add Tests

Create `test_new_entity.py`:

```python
"""Tests for new entity extraction."""

import json
from pathlib import Path
from src.extraction.new_entity import extract_new_entity
from src.grok_client import GrokClient

def test_extract_new_entity():
    """Test new entity extraction."""
    # Setup
    event_file = Path("test_data/chapter1-event.json")
    output_dir = Path("test_output")
    client = GrokClient(Path("test_cache"))
    
    # Execute
    result = extract_new_entity(event_file, client, output_dir)
    
    # Verify
    assert result.exists()
    with open(result) as f:
        data = json.load(f)
    assert "entities" in data
```

---

## Debugging

### Enable Trace Logging

```bash
python3 phase2_extract.py --log-level TRACE
```

### Inspect Cache

```bash
python3 scripts/review_cache.py
```

### Clear Cache

```python
from src.grok_client import GrokClient
from pathlib import Path

client = GrokClient(Path("cache/api"))
client.clear_cache("events")  # Clear specific type
client.clear_cache()          # Clear all
```

### Manual Extraction

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

client = GrokClient(Path("cache/api"))
event_file = extract_events(
    Path("output/Book/chapter1-parsed.json"),
    client,
    Path("output/Book")
)
print(f"Extracted: {event_file}")
```

---

## Testing

### Unit Tests

```bash
# Run all tests
python3 -m pytest

# Run specific test
python3 -m pytest test_new_entity.py

# With coverage
python3 -m pytest --cov=src
```

### Integration Tests

```bash
# Test full pipeline
python3 test_phase2_setup.py
```

### Manual Testing

```bash
# Test on single chapter
python3 -c "
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

client = GrokClient(Path('cache/api'))
extract_events(
    Path('output/BreakoutAndPursuit/chapter1a-parsed.json'),
    client,
    Path('output/BreakoutAndPursuit')
)
"
```

---

## Performance Optimization

### Caching Strategy

All API calls are cached automatically:

```python
# First call - hits API
result1 = client.extract_structured(schema, prompt)

# Second call - uses cache
result2 = client.extract_structured(schema, prompt)
```

### Skip Logic

Phase 2 skips already-processed files:

```python
# Events/dates/places: Skip if output exists
if output_file.exists():
    logger.info(f"Skipping {output_file}")
    return output_file

# People/groups: Skip if newer than event file
if people_dir.exists() and people_dir.stat().st_mtime > event_file.stat().st_mtime:
    logger.info("Skipping people extraction")
    return people_dir
```

### Batch Processing

Process multiple files efficiently:

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

client = GrokClient(Path("cache/api"))

for parsed_file in Path("output/Book").glob("*-parsed.json"):
    extract_events(parsed_file, client, parsed_file.parent)
```

---

## Common Issues

### Issue: API Rate Limiting

**Solution:** Use caching and implement backoff:

```python
import time
from src.grok_client import GrokAPIError

try:
    result = client.extract_structured(schema, prompt)
except GrokAPIError as e:
    if "rate limit" in str(e).lower():
        time.sleep(60)
        result = client.extract_structured(schema, prompt)
```

### Issue: Invalid ULID

**Solution:** Use helper function:

```python
from src.schemas import generate_ulid

# Don't generate manually
ulid = generate_ulid()
```

### Issue: Duplicate Detection False Positives

**Solution:** Add to exclusion list:

```python
# In merge_duplicate_people.py, choose "exclude" option
# Or manually edit output/people/not_duplicates.json
[
  ["PersonID1", "PersonID2"]
]
```

### Issue: Memory Usage

**Solution:** Process in batches and clear cache:

```python
for batch in batches(files, size=10):
    for file in batch:
        extract_events(file, client, output_dir)
    # Optionally clear cache
    client.clear_cache("events")
```

---

## Contributing

### Code Review Checklist

- [ ] Passes pylint (9.9-10/10)
- [ ] Passes mypy
- [ ] Passes bandit
- [ ] Formatted with black
- [ ] Includes docstrings
- [ ] Includes type hints
- [ ] Handles errors gracefully
- [ ] Uses caching where appropriate
- [ ] Updates documentation

### Documentation Standards

- Update relevant docs in `docs/current/`
- Include code examples
- Document all parameters and return values
- Add to API reference if public API

### Commit Messages

```
feat: Add new entity extraction
fix: Correct duplicate detection logic
docs: Update API reference
refactor: Simplify caching logic
test: Add integration tests
```

---

## Resources

### Internal Documentation
- [PIPELINE.md](PIPELINE.md) - Pipeline overview
- [CODE_ARCHITECTURE.md](CODE_ARCHITECTURE.md) - Code structure
- [API_REFERENCE.md](API_REFERENCE.md) - API documentation

### External Resources
- [Grok API Documentation](https://docs.x.ai/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [ULID Specification](https://github.com/ulid/spec)

### Tools
- [Pylint](https://pylint.org/)
- [Mypy](https://mypy-lang.org/)
- [Bandit](https://bandit.readthedocs.io/)
- [Black](https://black.readthedocs.io/)
- [Radon](https://radon.readthedocs.io/)
