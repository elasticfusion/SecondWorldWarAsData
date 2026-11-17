# Data Enrichment Service Design Specification

## Overview

A modular, extensible system for enriching structured data records by extracting specific attributes, querying an external API for enrichment data, caching results, and persisting enriched outputs.

## Core Objectives

1. Extract target attributes from local source data records
2. Query external API with extracted attributes and contextual information
3. Cache API responses to minimize redundant requests
4. Validate and parse API responses
5. Implement retry logic and fallback mechanisms
6. Support batch processing and single-record processing
7. Provide comprehensive logging and error tracking

## Architecture

### Module Organization

```
data_enrichment_service/
├── __main__.py              # CLI entry point and orchestration
├── api.py                   # External API communication
├── cache.py                 # Caching layer
├── extraction.py            # Attribute extraction from records
├── processing.py            # Core enrichment workflow
├── prompt_assembly.py       # Query construction
├── paths.py                 # Path resolution and validation
├── logging_setup.py         # Logging configuration
├── json_validator.py        # Data validation
├── batch_validator.py       # Batch validation utilities
└── fixer.py                 # Data repair utilities
```

### Key Components

#### 1. Extraction Module (`extraction.py`)

**Purpose**: Extract target attributes from source data JSON records

**Responsibilities**:
- Parse source data structure
- Identify and extract target attributes
- Handle multiple attribute name variations
- Return deduplicated, sorted attribute list

**Interface**:
```python
def extract_attributes(record: dict) -> List[str]:
    """Extract target attributes from a data record."""
```

#### 2. API Module (`api.py`)

**Purpose**: Manage external API communication with resilience

**Responsibilities**:
- Execute API calls with retry logic (exponential backoff)
- Parse API responses in multiple formats (JSON, code blocks, nested structures)
- Handle malformed responses gracefully
- Log API interactions for debugging

**Key Features**:
- Retry decorator with configurable attempts and backoff
- Multiple JSON extraction strategies (pure JSON, code blocks, brace matching)
- Fallback response generation for parsing failures

**Interface**:
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call_api(prompt: str) -> dict:
    """Call external API with automatic retry."""

def parse_response(content: str) -> dict:
    """Parse API response from various formats."""
```

#### 3. Cache Module (`cache.py`)

**Purpose**: Reduce API calls through intelligent caching

**Responsibilities**:
- Store enrichment results locally
- Retrieve cached results by normalized key
- Sanitize cache keys to prevent path traversal
- Handle cache misses gracefully

**Key Features**:
- Normalized key generation (accent removal, lowercase)
- Path traversal prevention via key sanitization
- JSON-based cache storage
- Transparent cache hit/miss handling

**Interface**:
```python
def get_cached_result(key: str, cache_dir: Path | None) -> dict | None:
    """Retrieve cached enrichment data."""

def store_cached_result(key: str, data: dict, cache_dir: Path | None) -> None:
    """Store enrichment data in cache."""

def extract_attribute_from_response(attr_name: str, response: list | dict) -> dict | None:
    """Extract specific attribute from API response."""
```

#### 4. Processing Module (`processing.py`)

**Purpose**: Orchestrate the enrichment workflow

**Responsibilities**:
- Load source data records
- Coordinate extraction, API calls, and caching
- Implement two-pass enrichment strategy
- Aggregate results and track failures
- Write enriched data to output files

**Key Features**:
- Two-pass enrichment (initial + fallback for low-confidence results)
- Rich context building from multiple record fields
- Failure tracking with detailed diagnostics
- Batch and single-record processing modes

**Interface**:
```python
def process_record(
    record: Dict,
    context: str,
    dry_run: bool,
    force_refresh: bool,
    cache_dir: Path | None = None
) -> List[Dict]:
    """Process a single record through enrichment pipeline."""

def process_batch(
    batch_id: str,
    dry_run: bool,
    force_refresh: bool,
    cache_dir_arg: Path | None = None
) -> None:
    """Process all unprocessed records in batch mode."""
```

#### 5. Prompt Assembly Module (`prompt_assembly.py`)

**Purpose**: Construct API queries from templates and data

**Responsibilities**:
- Load prompt templates from configuration
- Assemble queries with record context
- Support template variable substitution
- Save prompts for audit/debugging

**Key Features**:
- Template-based query construction
- Configurable assembly order
- Prompt persistence for traceability
- Variable substitution with context

**Interface**:
```python
def assemble_and_save_prompt(
    attribute: str,
    context: str,
    review_folder: Path,
    dry_run: bool
) -> str:
    """Assemble prompt from templates and save for audit."""
```

#### 6. Validation Module (`json_validator.py`)

**Purpose**: Ensure data integrity throughout the pipeline

**Responsibilities**:
- Validate source data structure
- Check required fields
- Validate data types
- Report validation errors with context

**Interface**:
```python
def validate_record_json(file_path: Path) -> dict:
    """Validate and load record data."""
```

#### 7. Paths Module (`paths.py`)

**Purpose**: Manage file system paths and directory structure

**Responsibilities**:
- Resolve batch/record folders
- Construct output file paths
- Check processing status
- Validate directory structure

**Interface**:
```python
def get_review_folder(batch_id: int, record_id: str) -> Path:
    """Resolve folder for a specific batch/record."""

def get_paths(batch_id: int, record_id: str, folder: Path) -> Dict[str, Path]:
    """Get all relevant file paths for a batch/record."""

def is_processed(folder: Path, base_name: str) -> bool:
    """Check if record has been enriched."""
```

#### 8. CLI Module (`__main__.py`)

**Purpose**: Command-line interface and orchestration

**Responsibilities**:
- Parse command-line arguments
- Coordinate module initialization
- Execute single or batch processing
- Handle validation and error reporting

**Key Features**:
- Single record processing
- Batch processing mode
- Dry-run capability
- Cache refresh override
- Comprehensive logging control
- Batch validation mode

**CLI Arguments**:
```
usage: data_enrichment_service [-h] [batch_id] [record_id]
                               [--batch] [--dry-run] [--force-refresh]
                               [--log-dir LOG_DIR] [--cache-dir CACHE_DIR]
                               [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                               [--show-prompts] [--validate-all]
```

## Data Flow

### Single Record Enrichment

```
1. Load Record
   ↓
2. Extract Attributes
   ↓
3. For Each Attribute:
   a. Check Cache
   b. If Cache Hit → Use Cached Result
   c. If Cache Miss:
      i. Assemble Query Prompt
      ii. Call External API
      iii. Parse Response
      iv. Store in Cache
   d. If Confidence = 0 (Fallback):
      i. Assemble Secondary Query
      ii. Call API Again
      iii. Update Cache if Improved
   ↓
4. Aggregate Results
   ↓
5. Write Output
   ↓
6. Track Failures
```

### Batch Processing

```
1. Discover Unprocessed Records
   ↓
2. For Each Record:
   → Execute Single Record Enrichment
   ↓
3. Aggregate Batch Results
   ↓
4. Generate Batch Report
```

## Configuration

### Environment Variables / Config File

```yaml
API_KEY: <external_api_key>
MODEL: <api_model_identifier>
API_ENDPOINT: <api_url>
API_TIMEOUT: <seconds>
API_TEMPERATURE: <0.0-1.0>
API_MAX_TOKENS: <integer>

ENRICHMENT_PROMPT_ASSEMBLY_ORDER: [template1, template2, ...]
ENRICHMENT_PROMPT_LOOKUP_STRATEGY: <strategy_name>
ENRICHMENT_PROMPT_TEMPLATES_DIR: <path>
ENRICHMENT_PROMPT_TARGET_FILENAME: <filename>
```

## Error Handling Strategy

### API Failures
- Automatic retry with exponential backoff (3 attempts, 2-30 second intervals)
- Graceful degradation with fallback response
- Detailed error logging for diagnostics

### Parsing Failures
- Multiple extraction strategies (pure JSON → code blocks → brace matching)
- Fallback response with confidence = 0.0
- Triggers secondary enrichment attempt

### Cache Failures
- Log warning but continue processing
- Treat as cache miss
- Proceed with API call

### Validation Failures
- Report validation errors with context
- Skip invalid records in batch mode
- Provide detailed error messages

## Caching Strategy

### Cache Key Generation
1. Normalize attribute name (remove accents, lowercase)
2. Sanitize for filesystem safety (replace special chars with underscores)
3. Store as `{sanitized_key}.json`

### Cache Invalidation
- Manual: `--force-refresh` flag
- Automatic: None (persistent cache)
- Scope: Per-attribute (not per-record)

### Cache Structure
```json
{
  "attribute": "value",
  "latitude": 40.7128,
  "longitude": -74.0060,
  "confidence": 0.95,
  "notes": "enrichment_metadata"
}
```

## Logging

### Log Levels
- **DEBUG**: Full prompt text, detailed API responses, cache operations
- **INFO**: Processing progress, cache hits/misses, API calls
- **WARNING**: Parsing failures, cache issues, low-confidence results
- **ERROR**: API failures, file I/O errors, validation failures
- **CRITICAL**: Fatal errors requiring exit

### Log Output
- Console: Configurable level (default: INFO)
- File: Optional, with timestamp and rotation

## Failure Tracking

### Failure Log Format
```json
[
  {
    "batch_id": 1,
    "record_id": "section_a",
    "attribute_index": 0,
    "attribute": "value",
    "notes": "reason_for_failure"
  }
]
```

### Failure Categories
- **Zero Confidence**: API returned low-confidence result
- **Not Found**: Attribute not found in API response
- **API Error**: API call failed after retries
- **Parse Error**: Response parsing failed

## Performance Considerations

### Optimization Opportunities
1. **Caching**: Reduces API calls by ~70-80% in typical workflows
2. **Batch Processing**: Amortizes startup costs
3. **Two-Pass Strategy**: Improves success rate without excessive API calls
4. **Prompt Caching**: Reuse base prompt across multiple attributes

### Scalability Limits
- Single-threaded processing (can be parallelized per record)
- API rate limits (configurable via backoff strategy)
- Disk I/O for cache operations (negligible for typical workloads)

## Security Considerations

### Input Validation
- Path traversal prevention via key sanitization
- JSON schema validation for source data
- API response validation before parsing

### Credential Management
- API keys via environment variables (not hardcoded)
- No credentials in logs or prompts
- Secure cache storage (local filesystem)

### Data Privacy
- Cache stored locally (not transmitted)
- Audit trail via prompt logging
- Failure tracking for compliance

## Extension Points

### Custom Extraction Logic
Implement `extract_attributes()` for domain-specific attribute identification

### Custom API Adapters
Extend `call_api()` for different API providers or authentication schemes

### Custom Response Parsers
Add strategies to `parse_response()` for additional response formats

### Custom Validation Rules
Extend `validate_record_json()` for domain-specific validation

### Custom Prompt Templates
Add templates to `ENRICHMENT_PROMPT_TEMPLATES_DIR` for different enrichment strategies

## Testing Strategy

### Unit Tests
- Extraction logic with various record structures
- Cache operations (hit, miss, sanitization)
- Response parsing with malformed inputs
- Path resolution and validation

### Integration Tests
- End-to-end single record enrichment
- Batch processing with multiple records
- Cache persistence across runs
- Error recovery and retry logic

### Validation Tests
- Batch validation mode
- JSON schema compliance
- Failure tracking accuracy

## Deployment

### Prerequisites
- Python 3.9+
- External API access and credentials
- Writable cache directory
- Template files in configured location

### Installation
```bash
pip install -r requirements.txt
```

### Configuration
1. Set environment variables or create config file
2. Prepare template files in `ENRICHMENT_PROMPT_TEMPLATES_DIR`
3. Ensure source data files exist in expected locations

### Execution
```bash
# Single record
python -m data_enrichment_service 1 section_a

# Batch processing
python -m data_enrichment_service --batch

# With options
python -m data_enrichment_service 1 section_a --dry-run --show-prompts --log-level DEBUG
```

## Monitoring and Observability

### Key Metrics
- Records processed (total, successful, failed)
- Cache hit rate
- API call count and latency
- Average confidence score
- Processing time per record

### Alerting
- API failures after retries
- Cache directory issues
- Validation failures
- High failure rate (>10%)

### Audit Trail
- Prompt text (if `--show-prompts` enabled)
- API responses (DEBUG level)
- Cache operations (DEBUG level)
- Failure details (always logged)
