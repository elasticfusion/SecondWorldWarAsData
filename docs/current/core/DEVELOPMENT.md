# Development Guide

**Last Updated:** 2026-11-16

## Setup

### Prerequisites
- Python 3.12+ (`pyproject.toml` `requires-python`; CI runs 3.12)
- Grok API key
- ~2GB disk space for cache
- Chrome or Chromium (for PDF conversion and web scraping)
- Go 1.21+ (for building OpenSERP search tool)

### Installation

```bash
# Clone repository
git clone <repo-url>
cd SecondWorldWarAsData

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install project in editable mode (enables imports without sys.path hacks)
pip install -e .
```

### Configuration

Copy the shipped defaults, then edit the copy (`config.yaml` is gitignored local state; see [CONFIGURATION.md](CONFIGURATION.md)):

```bash
cp config.yaml.example config.yaml
```

The Grok model is `GROK_MODEL` env, else `api.grok.model` (`grok-4.3`). The API key is never read from `config.yaml`; it comes from the `GROK_API_KEY` env var (or the `.env` file below, or Secrets Manager in AWS).

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

Normally: upload the source (PDF, EPUB, DOCX, HTML, video) to `contentrepository/` and let Phase 0 produce the chapters (see [PIPELINE.md](PIPELINE.md)). A book's slug is its filename stem and never changes (`src/ingestion/book_slug.py`). To work locally with hand-made chapters, place markdown files in `contentrepository/`:

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

# Merge duplicates: exact duplicates are auto-merged at the end of Phase 2; the rest go
# through the Dedup Review UI (lambda_handlers/dedup_ui_handler.py, src/dedup/merge.py).
# scripts/merge_duplicate_people.py no longer exists.

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

Run the full gate before every commit; it mirrors CI (`.github/workflows/lint.yml` + `tests.yml`) and exits non-zero on any failure:

```bash
bash scripts/gate.sh             # linters + tests
bash scripts/gate.sh --no-tests  # linters only
```

It runs black (check), pylint (`--fail-under=5.0` over `src/ lambda_handlers/`), mypy, basedpyright (baseline in `.basedpyright/baseline.json`; only new errors fail), bandit, ruff (B rules), radon (no new C-or-worse blocks; grandfathered ones in `scripts/.radon_inherent`), vulture, the unreachable-modules check, cfn-lint, a JSON lint, pip-audit, then `pytest tests/ -m "not slow and not requires_api"`. Single tools, e.g. `python3 -m black file.py` or `python3 -m pylint file.py`, are fine while iterating. See [TESTING.md](TESTING.md).

### Security Scanning (Deploy)

The deployment pipeline runs additional security checks:

```bash
# Dockerfile linting
hadolint Dockerfile

# CloudFormation linting
cfn-lint cloudformation/*.yaml

# CloudFormation security analysis
cfn-nag cloudformation/*.yaml

# Container image vulnerability scanning
trivy image wwii-pipeline:latest
```

These are run automatically by `scripts/deploy_all.sh` before pushing images.

### Standards

- **Pylint**: `--fail-under=5.0` in the gate; keep new code clean (disable only specific warnings)
- **Mypy / basedpyright**: no new errors
- **Bandit**: no MEDIUM or higher (`-ll`)
- **Radon**: no new C-or-worse blocks
- **Black**: formatted
- Tests for all new code, observability (log lines / metrics) built in, and provenance (source citation) on all generated data

---

## Adding New Extraction Types

Follow an existing small extractor (for example `src/extraction/casualties.py` or `logistics.py`) rather than writing from scratch; the conventions below come from them.

### 1. Create the schema and register its version

Add `src/schemas/<entity>_output.py` with the entity's JSON Schema (copy a neighbour such as `casualties_output.py`; build fields with the helpers in `src/schemas/__init__.py` such as `ulid_field`) and register the entity's version in `ENTITY_SCHEMA_VERSIONS`. Each entity is versioned independently. Do not add models to `src/schemas.py`: the `src/schemas/` package shadows it. The Pydantic model that validates Grok's answer sits in the extractor module.

### 2. Create the prompt

Add `prompts/<entity>.yaml` (`system_prompt`, `prompt_template`, `schema`, `rules`) and render it with `render_prompt("<entity>", text=...)` from `src/utils/prompt_loader.py`. Do not inline prompt text in Python. See [PROMPT_MANAGEMENT.md](PROMPT_MANAGEMENT.md).

### 3. Create the extraction module

In `src/extraction/<entity>.py`. Entity discovery runs over fixed 3-paragraph windows of the raw parsed text (`src/extraction/windows.py`: `chapter_windows`, `window_event_for`), never over the events' summaries. Each answer item must carry a quote that is checked against the source (`windows.settle`) so every record has provenance; an item whose quote the source does not support is dropped with `quote_unsupported`. Requests are queued on the `GrokClient` and answered by xAI batch rounds, so the extractor must be safe to call again after the batch answers (idempotent, cache-first).

```python
def extract_new_entity(event_file: Path, grok_client: GrokClient, output_dir: Path,
                       parsed_file: Optional[Path] = None, max_retries: int = 3) -> Optional[Path]:
    ...
    answer = grok_client.extract_json(prompt, system_prompt=system, cache_type="new_entity", book=book)
```

Write entity files with the shared helpers (`src/utils/entity_records.py`, `src/utils/file_lock.py` `locked_json`) so mentions are de-duplicated by `mention_identity` and index files are updated under a lock.

### 4. Wire it into Phase 2

Call it from the extraction orchestration (`src/extraction/batch_parallel.py` for the core group, or the optional-entity step in `phase2_extract.py`), behind a `config.yaml` flag if it is optional (add the flag to `config.yaml.example`, [CONFIGURATION.md](CONFIGURATION.md) and the CloudFormation/profile overlays if they manage it).

### 5. Add Tests

Create `tests/unit/test_new_entity.py` (pytest discovers `tests/`; use `tmp_path` and a stubbed `GrokClient`, never the real API; see [TESTING.md](TESTING.md)):

```python
"""Tests for new entity extraction."""

import json
from pathlib import Path
from src.extraction.new_entity import extract_new_entity

def test_extract_new_entity():
    """Test new entity extraction."""
    # Setup
    event_file = Path("test_data/chapter1-event.json")
    output_dir = Path("test_output")
    client = StubGrok()  # a fake with extract_json(...) returning canned JSON
    
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
python3 phase2_extract.py --log-level TRACE   # TRACE is level 5, below DEBUG
```

### Inspect Cache

```bash
python3 scripts/utils/review_cache.py
```

### Clear Cache

```python
from src.grok_client import GrokClient
from pathlib import Path

client = GrokClient(Path("cache/api"))
client.clear_cache_entry(prompt, cache_type="events")  # remove one entry (prompt, system_prompt, book and temperature must match the original request)
# To clear a whole kind locally: delete cache/api/<kind>/ ; in AWS the cache is the DynamoDB table
```

### Manual Extraction

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

client = GrokClient(Path("cache/api"))
event_file = extract_events(
    Path("output/content/Book/chapter1-parsed.json"),
    client,
    Path("output/content/Book")
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
python3 -m pytest tests/unit/test_new_entity.py

# Same selection the gate and CI use
python3 -m pytest tests/ -m "not slow and not requires_api" -q
```

### Integration Tests

```bash
# multi-component tests are marked and skipped by default
python3 -m pytest -m integration
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
    Path('output/content/BreakoutAndPursuit/chapter1a-parsed.json'),
    client,
    Path('output/content/BreakoutAndPursuit')
)
"
```

---

## Performance Optimization

### Caching Strategy

All API calls are cached automatically:

```python
# First call - hits API
result1 = client.extract_structured(prompt, schema)

# Second call - uses cache
result2 = client.extract_structured(prompt, schema)
```

### Skip Logic

Phase 2 does not redo finished work. Answers are cached per request (cache-first in `GrokClient`), and people extraction records each event it has handled in `<people_dir>/.processed_events.json` (`src/extraction/people.py`) and logs `Event already processed for people extraction: <file>`. Re-extracting a chapter mints new Sub-eventIDs, so purge it first (`scripts/purge_chapter_event.py`). `processing.force_reprocess: true` bypasses the skips (expensive).

### Batch Processing

Process multiple files efficiently:

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

client = GrokClient(Path("cache/api"))

for parsed_file in Path("output/content/Book").glob("*-parsed.json"):
    extract_events(parsed_file, client, parsed_file.parent)
```

---

## Common Issues

### Issue: API Rate Limiting

**Solution:** The client already rate-limits (`api.calls_per_minute`) and retries with backoff; in AWS Phase 2 uses the batch API, which has no per-minute limit. Lower `calls_per_minute` or add your own backoff for ad-hoc scripts:

```python
import time
from src.grok_client import GrokAPIError

try:
    result = client.extract_structured(prompt, schema)
except GrokAPIError as e:
    if "rate limit" in str(e).lower():
        time.sleep(60)
        result = client.extract_structured(prompt, schema)
```

### Issue: Invalid ULID

**Solution:** Never hand-write or let the model invent ids. EventID / Sub-eventID / BookID come from `src/utils/deterministic_ids.py` (`event_id`, `sub_event_id`, `book_id`); other ids are `str(ulid.new())`. `generate_ulid` (old `src/schemas.py`) no longer imports. See [ULID_IMPLEMENTATION.md](ULID_IMPLEMENTATION.md).

### Issue: Duplicate Detection False Positives

**Solution:** Mark the pair "not duplicates" in the Dedup Review UI. Decisions are stored by `src/dedup/exclusions.py` (`exclusion#<type>#<file1>#<file2>` rows in DynamoDB in AWS mode, `not_duplicates.json` locally; name-based `name_exclusion#...` rows survive file recreation), so they persist across runs.

### Issue: Memory Usage

**Solution:** Process in batches and clear cache:

```python
for batch in batches(files, size=10):
    for file in batch:
        extract_events(file, client, output_dir)
    # Optionally clear cache
    client.clear_cache_entry(prompt, cache_type="events")
```

---

## Contributing

### Code Review Checklist

- [ ] `bash scripts/gate.sh` passes
- [ ] Tests added
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
