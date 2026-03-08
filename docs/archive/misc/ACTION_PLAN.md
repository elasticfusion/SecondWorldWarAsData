# Action Plan: Next Steps
**Date:** February 20, 2026  
**Project:** Second World War as Data

---

## Priority 1: Fix Timeout Issues (Critical)

### Problem
6 of 13 files are timing out after 3 minutes during event extraction.

### Root Cause
Large documents with many paragraphs exceed the 60-second API timeout.

### Solution Options

#### Option A: Increase Timeout (Quick Fix)
```python
# In config.yaml
api:
  grok:
    timeout: 300  # Increase from 60 to 300 seconds (5 minutes)
```

**Pros:** Simple, one-line change  
**Cons:** Doesn't solve underlying issue, just delays it

#### Option B: Implement Document Chunking (Better Solution)
```python
# In src/extraction/events.py
def chunk_paragraphs(paragraphs, max_chunk_size=20):
    """Split paragraphs into smaller chunks for processing."""
    for i in range(0, len(paragraphs), max_chunk_size):
        yield paragraphs[i:i + max_chunk_size]

def extract_events_from_parsed(parsed_data, grok_client):
    paragraphs = parsed_data["paragraphs"]
    
    # If document is large, process in chunks
    if len(paragraphs) > 30:
        all_events = []
        for chunk in chunk_paragraphs(paragraphs, max_chunk_size=20):
            events = process_chunk(chunk, grok_client)
            all_events.extend(events)
        return merge_events(all_events)
    else:
        # Process normally for small documents
        return process_full_document(paragraphs, grok_client)
```

**Pros:** Scalable, handles any document size  
**Cons:** More complex, requires event merging logic

### Recommended Action
**Start with Option A** (increase timeout to 300s), then implement **Option B** for long-term scalability.

### Implementation Steps
1. Update `config.yaml` timeout to 300
2. Reprocess the 6 failed files
3. Monitor results
4. If still timing out, implement chunking

---

## Priority 2: Add Unit Tests (Critical)

### Problem
No test coverage means regression risk when making changes.

### Solution
Add pytest-based unit tests for core functionality.

### Implementation Steps

#### Step 1: Install Test Dependencies
```bash
pip install pytest pytest-cov pytest-mock
echo "pytest>=7.0" >> requirements-dev.txt
echo "pytest-cov>=4.0" >> requirements-dev.txt
echo "pytest-mock>=3.0" >> requirements-dev.txt
```

#### Step 2: Create Test Structure
```bash
mkdir -p tests
touch tests/__init__.py
touch tests/test_parser.py
touch tests/test_grok_client.py
touch tests/test_extractors.py
touch tests/test_schemas.py
```

#### Step 3: Write Basic Tests
```python
# tests/test_parser.py
import pytest
from src.parser import parse_content_file

def test_parse_simple_paragraph():
    content = "This is a test paragraph."
    result = parse_content_file(content)
    assert len(result["paragraphs"]) == 1
    assert result["paragraphs"][0]["text"] == content

def test_parse_multiple_paragraphs():
    content = "Paragraph 1.\n\nParagraph 2."
    result = parse_content_file(content)
    assert len(result["paragraphs"]) == 2

# tests/test_grok_client.py
import pytest
from src.grok_client import GrokClient

@pytest.fixture
def mock_client(mocker):
    mocker.patch('httpx.Client')
    return GrokClient(api_key="test", cache_dir="test_cache")

def test_cache_hit(mock_client, mocker):
    # Test that cached responses are returned
    pass

def test_retry_on_5xx(mock_client, mocker):
    # Test retry logic
    pass
```

#### Step 4: Run Tests
```bash
pytest tests/ -v --cov=src --cov-report=html
```

### Target Coverage
- **Phase 1:** 80% coverage
- **Phase 2:** 70% coverage (harder to test API calls)

---

## Priority 3: Complete Phase 2 Entity Extraction

### Current Status
- ✅ Events: Working
- ⏳ Dates: Module exists, not integrated
- ⏳ Places: Module exists, not integrated
- ⏳ People: Module exists, not integrated
- ⏳ Weather: Module exists, not integrated

### Implementation Plan

#### Step 1: Integrate Date Extraction
```python
# In phase2_extract.py
from src.extraction.dates import extract_dates_from_events

def main():
    # ... existing event extraction ...
    
    # Add date extraction
    if event_file.exists():
        logger.info(f"Extracting dates from {event_file.name}")
        dates = extract_dates_from_events(event_data, grok_client)
        date_file = output_dir / f"{stem}-dates.json"
        date_file.write_text(json.dumps(dates, indent=2))
        logger.info(f"  Saved: {date_file.name}")
```

#### Step 2: Integrate Place Extraction
```python
# Similar pattern for places
from src.extraction.places import extract_places_from_events

places = extract_places_from_events(event_data, grok_client)
place_file = output_dir / f"{stem}-places.json"
```

#### Step 3: Integrate People Extraction
```python
# People are centrally managed
from src.extraction.people import extract_people_from_events

people = extract_people_from_events(event_data, grok_client)
# Append to central people.json file
update_central_people_file(people)
```

#### Step 4: Integrate Weather Extraction
```python
from src.extraction.weather import extract_weather_from_events

weather = extract_weather_from_events(event_data, grok_client)
weather_file = output_dir / f"{stem}-weather.json"
```

### Expected Output Structure
```
output/BreakoutAndPursuit/
├── chapter1a-parsed.json    # Phase 1
├── chapter1a-event.json     # Phase 2 (done)
├── chapter1a-dates.json     # Phase 2 (todo)
├── chapter1a-places.json    # Phase 2 (todo)
├── chapter1a-weather.json   # Phase 2 (todo)
└── ...

output/
├── people.json              # Central people database
└── peoplegroups.json        # Central groups database
```

---

## Priority 4: Generate Validation Scripts

### Problem
Requirements specify JQ scripts for validation, but they're not being generated.

### Solution
Add script generation to each extractor.

### Implementation

#### Step 1: Create Script Generator Module
```python
# src/utils/script_generator.py
from pathlib import Path

def generate_jq_validation_script(entity_type: str, output_file: Path):
    """Generate JQ script to validate entity JSON."""
    
    scripts = {
        "events": """
# Validate event structure
jq '.Sub-events[] | select(.["Sub-eventID"] | test("^[0-9A-HJKMNP-TV-Z]{26}$") | not)' {file}
        """,
        "dates": """
# Validate date ULIDs
jq '.dates[] | select(.DateID | test("^[0-9A-HJKMNP-TV-Z]{26}$") | not)' {file}
        """,
        "places": """
# Validate place coordinates
jq '.places[] | select(.latitude == null or .longitude == null)' {file}
        """
    }
    
    script = scripts.get(entity_type, "")
    script_file = output_file.parent / f"validate_{entity_type}.sh"
    script_file.write_text(f"#!/bin/bash\n{script}")
    script_file.chmod(0o755)
    return script_file

def generate_download_script(urls: list, output_dir: Path):
    """Generate script to download supporting materials."""
    
    script = "#!/bin/bash\n\n"
    for url in urls:
        filename = Path(url).name
        script += f"curl -o {output_dir}/{filename} '{url}'\n"
    
    script_file = output_dir / "download_materials.sh"
    script_file.write_text(script)
    script_file.chmod(0o755)
    return script_file
```

#### Step 2: Integrate into Extractors
```python
# In src/extraction/events.py
from src.utils.script_generator import generate_jq_validation_script

def extract_events_from_parsed(parsed_data, grok_client):
    # ... existing extraction ...
    
    # Generate validation script
    output_file = Path(f"output/{book}/{chapter}-event.json")
    generate_jq_validation_script("events", output_file)
    
    return result
```

---

## Priority 5: Add Monitoring and Metrics

### Problem
No visibility into processing performance, API usage, or error rates.

### Solution
Add structured logging and metrics collection.

### Implementation

#### Step 1: Add Metrics Collection
```python
# src/utils/metrics.py
from dataclasses import dataclass
from datetime import datetime
import json

@dataclass
class ProcessingMetrics:
    start_time: datetime
    end_time: datetime
    files_processed: int
    files_succeeded: int
    files_failed: int
    api_calls_made: int
    api_calls_cached: int
    total_paragraphs: int
    
    def to_dict(self):
        return {
            "duration_seconds": (self.end_time - self.start_time).total_seconds(),
            "success_rate": self.files_succeeded / self.files_processed if self.files_processed > 0 else 0,
            "cache_hit_rate": self.api_calls_cached / (self.api_calls_made + self.api_calls_cached) if (self.api_calls_made + self.api_calls_cached) > 0 else 0,
            **self.__dict__
        }
    
    def save(self, path: Path):
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
```

#### Step 2: Integrate into Pipeline
```python
# In phase2_extract.py
from src.utils.metrics import ProcessingMetrics

def main():
    metrics = ProcessingMetrics(
        start_time=datetime.now(),
        files_processed=0,
        files_succeeded=0,
        files_failed=0,
        api_calls_made=0,
        api_calls_cached=0,
        total_paragraphs=0
    )
    
    # ... processing loop ...
    
    metrics.end_time = datetime.now()
    metrics.save(Path("logs/metrics.json"))
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"Processing Complete")
    print(f"{'='*60}")
    print(f"Duration: {metrics.end_time - metrics.start_time}")
    print(f"Files: {metrics.files_succeeded}/{metrics.files_processed} succeeded")
    print(f"Success Rate: {metrics.to_dict()['success_rate']:.1%}")
    print(f"Cache Hit Rate: {metrics.to_dict()['cache_hit_rate']:.1%}")
```

---

## Priority 6: Dependency Management

### Problem
Dependencies not pinned, no vulnerability scanning.

### Solution
Pin versions and add security scanning.

### Implementation

#### Step 1: Pin Dependency Versions
```bash
# Generate pinned requirements
pip freeze > requirements.txt

# Or use pip-tools
pip install pip-tools
pip-compile requirements.in -o requirements.txt
```

#### Step 2: Add Security Scanning
```bash
# Install safety
pip install safety

# Scan for vulnerabilities
safety check

# Add to requirements-dev.txt
echo "safety>=2.0" >> requirements-dev.txt
```

#### Step 3: Add Pre-commit Hooks
```bash
# Install pre-commit
pip install pre-commit

# Create .pre-commit-config.yaml
cat > .pre-commit-config.yaml << 'EOF'
repos:
  - repo: https://github.com/psf/black
    rev: 23.0.0
    hooks:
      - id: black
  
  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort
  
  - repo: https://github.com/pycqa/flake8
    rev: 6.0.0
    hooks:
      - id: flake8
  
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.0.0
    hooks:
      - id: mypy
EOF

# Install hooks
pre-commit install
```

---

## Timeline

### Week 1 (Immediate)
- [ ] Fix timeout issues (increase to 300s)
- [ ] Reprocess failed files
- [ ] Add basic unit tests (parser, schemas)

### Week 2
- [ ] Integrate date extraction
- [ ] Integrate place extraction
- [ ] Add integration tests

### Week 3
- [ ] Integrate people extraction
- [ ] Integrate weather extraction
- [ ] Generate JQ validation scripts

### Week 4
- [ ] Add monitoring and metrics
- [ ] Pin dependencies
- [ ] Add pre-commit hooks
- [ ] Documentation updates

---

## Success Criteria

### Phase 2 Complete When:
- ✅ All 13 files process successfully (no timeouts)
- ✅ All entity types extracted (events, dates, places, people, weather)
- ✅ JQ validation scripts generated
- ✅ Download scripts generated
- ✅ Test coverage >70%
- ✅ All quality checks passing

---

## Questions to Consider

1. **API Costs:** What's the budget for Grok API calls?
2. **Processing Schedule:** How often will this run? (daily, weekly, on-demand)
3. **Data Storage:** Should we move from JSON files to a database?
4. **User Interface:** Do we need a web dashboard for monitoring?
5. **Deployment:** Where will this run? (local, cloud, container)

---

**Next Step:** Start with Priority 1 (fix timeouts) and work down the list.

*Generated by Kiro AI Assistant*
