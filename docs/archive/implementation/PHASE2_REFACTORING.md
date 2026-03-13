# Phase 2 Pipeline Refactoring Plan

## Current State

**File:** `phase2_extract.py`
**Size:** 560 lines (530 lines in main())
**Complexity:** F (57) - Extremely high
**Statements:** 294 - Monolithic procedural script
**Pylint Score:** 5.54/10

## Complexity Issues

### Main Function Analysis

**Complexity: F (57)**
- 294 statements in single function
- 63 branches (if/else/try/except)
- Sequential processing of 8+ extraction stages
- Inline error handling throughout
- Mixed concerns: config, validation, extraction, reporting

### Current Structure

```
main():
  1. Argument parsing (20 lines)
  2. Configuration loading (30 lines)
  3. Metadata completion (40 lines)
  4. Grok client initialization (20 lines)
  5. Event extraction loop (80 lines)
  6. Date extraction loop (60 lines)
  7. Place extraction loop (60 lines)
  8. People extraction loop (60 lines)
  9. People groups extraction loop (60 lines)
  10. Equipment extraction loop (60 lines)
  11. Map import (20 lines)
  12. Duplicate analysis (30 lines)
  13. Related groups analysis (30 lines)
```

## Recommended Refactoring

### Phase 1: Extract Configuration & Setup

Create helper functions:
```python
def parse_arguments() -> argparse.Namespace
def load_pipeline_config(args) -> dict
def initialize_grok_client(config) -> GrokClient
def complete_metadata(content_dir, grok_client) -> int
```

**Benefit:** Reduces main() by ~110 lines

### Phase 2: Extract Processing Stages

Create stage functions:
```python
def process_events_stage(parsed_files, grok_client, output_root) -> List[Path]
def process_dates_stage(event_files, grok_client, output_root) -> int
def process_places_stage(event_files, grok_client, output_root) -> int
def process_people_stage(event_files, grok_client, output_root) -> int
def process_groups_stage(event_files, grok_client, output_root) -> int
def process_equipment_stage(event_files, grok_client, output_root, config) -> int
def process_maps_stage(output_root, config) -> int
```

**Benefit:** Reduces main() by ~360 lines

### Phase 3: Extract Analysis & Reporting

Create analysis functions:
```python
def analyze_duplicates(people_dir) -> None
def analyze_related_groups(groups_dir) -> None
def log_pipeline_summary(stats: dict) -> None
```

**Benefit:** Reduces main() by ~60 lines

### Phase 4: Create Pipeline Orchestrator

Final main() structure:
```python
def main():
    """Phase 2 pipeline orchestrator."""
    args = parse_arguments()
    config = load_pipeline_config(args)
    grok_client = initialize_grok_client(config)
    
    # Setup
    complete_metadata(config['content_dir'], grok_client)
    
    # Extraction stages
    event_files = process_events_stage(...)
    stats = {
        'dates': process_dates_stage(...),
        'places': process_places_stage(...),
        'people': process_people_stage(...),
        'groups': process_groups_stage(...),
        'equipment': process_equipment_stage(...),
        'maps': process_maps_stage(...),
    }
    
    # Analysis
    analyze_duplicates(...)
    analyze_related_groups(...)
    
    # Summary
    log_pipeline_summary(stats)
```

**Result:** main() reduced to ~50 lines, complexity A-B

### Phase 5: Error Handling Strategy

Centralize error handling:
```python
class PipelineError(Exception):
    """Pipeline processing error."""

def safe_stage_execution(stage_func, stage_name, *args, **kwargs):
    """Execute stage with consistent error handling."""
    try:
        logger.info(f"Starting {stage_name}...")
        result = stage_func(*args, **kwargs)
        logger.info(f"✓ {stage_name} complete")
        return result
    except Exception as e:
        logger.error(f"✗ {stage_name} failed: {e}")
        raise PipelineError(f"{stage_name} failed") from e
```

## Implementation Priority

1. **High Priority:** Extract configuration & setup (Phase 1)
2. **High Priority:** Extract processing stages (Phase 2)
3. **Medium Priority:** Extract analysis functions (Phase 3)
4. **Medium Priority:** Create orchestrator (Phase 4)
5. **Low Priority:** Centralize error handling (Phase 5)

## Testing Requirements

- Unit tests for each extracted function
- Integration tests for stage execution
- End-to-end pipeline test with sample data
- Regression tests against current output

## Estimated Effort

- Phase 1: 2-3 hours
- Phase 2: 4-6 hours
- Phase 3: 1-2 hours
- Phase 4: 1-2 hours
- Phase 5: 1-2 hours
- Testing: 4-6 hours

**Total:** 13-21 hours

## Benefits

- **Maintainability:** Each function has single responsibility
- **Testability:** Individual stages can be tested in isolation
- **Readability:** main() becomes high-level orchestration
- **Reusability:** Stage functions can be used independently
- **Debugging:** Easier to identify and fix issues in specific stages

## Risks

- Breaking changes during active processing
- Potential behavior changes if error handling differs
- Need to maintain backward compatibility with existing data

## Notes

- Current script is functional and processing Phase 2
- Refactoring should be done when pipeline is idle
- Consider feature freeze during refactoring
- Maintain logging format for consistency
- Keep command-line interface unchanged
