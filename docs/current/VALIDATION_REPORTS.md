# Validation Reports

Generate HTML and JSON reports for validation results with historical tracking.

## Features

- **HTML Reports** - Beautiful, readable reports with metrics and error details
- **JSON Reports** - Machine-readable reports for automation
- **Validation History** - Track validation results over time
- **Trend Analysis** - Analyze validation trends and success rates
- **CI/CD Integration** - Exit codes and automation-friendly

## Quick Start

### Generate Validation Report

```bash
# HTML report (default)
python scripts/validation_report.py validate output/people --schema people

# JSON report
python scripts/validation_report.py validate output/people --schema people --format json

# Save to file
python scripts/validation_report.py validate output/people --schema people --output report.html

# Save to history
python scripts/validation_report.py validate output/people --schema people --save-history
```

### View Trends

```bash
# All schemas
python scripts/validation_report.py trends --output trends.html

# Specific schema
python scripts/validation_report.py trends --schema people --output people_trends.html
```

## HTML Report Features

### Summary Metrics
- Total files validated
- Valid file count
- Invalid file count
- Success rate percentage

### Error Details
- File name
- Error message
- Formatted for readability

### Visual Design
- Color-coded metrics (green for success, red for errors)
- Responsive layout
- Clean, professional appearance

### Example

See `docs/examples/validation_report_example.html` for a sample report.

## JSON Report Format

```json
{
  "timestamp": "2026-03-09T12:00:00",
  "schema": "people",
  "directory": "output/people",
  "summary": {
    "total": 150,
    "valid": 145,
    "invalid": 5,
    "success_rate": "96.7%"
  },
  "errors": [
    {
      "file": "person_001.json",
      "error": "'name' is a required property"
    }
  ]
}
```

## Validation History

### Enable History Tracking

```bash
python scripts/validation_report.py validate output/people \
  --schema people \
  --save-history \
  --history-file validation_history.json
```

### History Format

```json
[
  {
    "timestamp": "2026-03-09T12:00:00",
    "schema": "people",
    "directory": "output/people",
    "total": 150,
    "valid": 145,
    "invalid": 5,
    "error_count": 5
  }
]
```

### History Features
- Automatic append to history file
- Keeps last 100 entries
- Per-schema tracking
- Timestamp for each run

## Trend Reports

### Generate Trends

```bash
# All schemas
python scripts/validation_report.py trends

# Specific schema
python scripts/validation_report.py trends --schema people

# Custom history file
python scripts/validation_report.py trends --history-file custom_history.json
```

### Trend Metrics
- Total validation runs
- Average success rate
- Recent validations (last 10)
- Success rate over time

### Trend Report Features
- Table of recent validations
- Color-coded success rates
- Timestamp tracking
- Directory information

## Programmatic Usage

### Generate Report

```python
from pathlib import Path
from src.utils.json_validator import validate_directory
from src.utils.validation_reports import generate_validation_report
from src.json_schemas import PEOPLE_SCHEMA

# Validate
results = validate_directory(Path("output/people"), PEOPLE_SCHEMA)

# Generate HTML report
html_report = generate_validation_report(
    results, "people", Path("output/people"), "html"
)

# Save
Path("report.html").write_text(html_report)
```

### Save to History

```python
from pathlib import Path
from src.utils.validation_reports import save_validation_history

save_validation_history(
    results,
    "people",
    Path("output/people"),
    Path("validation_history.json")
)
```

### Generate Trends

```python
from pathlib import Path
from src.utils.validation_reports import generate_trend_report

html = generate_trend_report(Path("validation_history.json"), "people")
Path("trends.html").write_text(html)
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Validate Data

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Validate People Data
        run: |
          python scripts/validation_report.py validate output/people \
            --schema people \
            --format html \
            --output validation_report.html \
            --save-history
      
      - name: Upload Report
        uses: actions/upload-artifact@v2
        with:
          name: validation-report
          path: validation_report.html
      
      - name: Generate Trends
        run: |
          python scripts/validation_report.py trends \
            --schema people \
            --output trends.html
      
      - name: Upload Trends
        uses: actions/upload-artifact@v2
        with:
          name: trends-report
          path: trends.html
```

### Exit Codes

- `0`: All files valid
- `1`: One or more files invalid

Use in scripts:

```bash
if python scripts/validation_report.py validate output/people --schema people; then
    echo "Validation passed!"
else
    echo "Validation failed!"
    exit 1
fi
```

## Use Cases

### 1. Pre-Deployment Validation

```bash
# Validate before deploying
python scripts/validation_report.py validate output/people \
  --schema people \
  --output pre_deploy_report.html \
  --save-history

# Check exit code
if [ $? -eq 0 ]; then
    echo "Ready to deploy"
else
    echo "Fix validation errors first"
    exit 1
fi
```

### 2. Nightly Validation

```bash
#!/bin/bash
# nightly_validation.sh

for schema in people equipment maps; do
    python scripts/validation_report.py validate output/$schema \
      --schema $schema \
      --output reports/${schema}_$(date +%Y%m%d).html \
      --save-history
done

# Generate trends
python scripts/validation_report.py trends \
  --output reports/trends_$(date +%Y%m%d).html
```

### 3. Data Quality Dashboard

```python
# Generate reports for all schemas
from pathlib import Path
from src.utils.schema_registry import get_registry
from src.utils.json_validator import validate_directory
from src.utils.validation_reports import generate_validation_report

registry = get_registry()
reports_dir = Path("reports")
reports_dir.mkdir(exist_ok=True)

for schema_name in registry.list_schemas():
    data_dir = Path(f"output/{schema_name}")
    if not data_dir.exists():
        continue
    
    schema = registry.get_schema(schema_name)
    results = validate_directory(data_dir, schema)
    
    report = generate_validation_report(results, schema_name, data_dir, "html")
    (reports_dir / f"{schema_name}.html").write_text(report)

print(f"Generated {len(list(reports_dir.glob('*.html')))} reports")
```

## Best Practices

### 1. Regular Validation

Run validation regularly to catch issues early:

```bash
# Daily cron job
0 2 * * * cd /path/to/project && python scripts/validation_report.py validate output/people --schema people --save-history
```

### 2. Track History

Always save to history for trend analysis:

```bash
--save-history --history-file validation_history.json
```

### 3. Review Trends

Check trends weekly to identify patterns:

```bash
python scripts/validation_report.py trends --output weekly_trends.html
```

### 4. Archive Reports

Keep reports for auditing:

```bash
python scripts/validation_report.py validate output/people \
  --schema people \
  --output reports/people_$(date +%Y%m%d_%H%M%S).html
```

## Customization

### Custom Report Styling

Modify `src/utils/validation_reports.py` to customize HTML styling:

```python
# Change colors, fonts, layout in _generate_html_report()
```

### Custom Metrics

Add custom metrics to reports:

```python
from src.utils.validation_reports import generate_validation_report

# Add custom data
results["custom_metric"] = calculate_custom_metric()

# Generate report
report = generate_validation_report(results, schema_name, directory, "json")
```

## Troubleshooting

### No History Found

```bash
# Initialize history
python scripts/validation_report.py validate output/people \
  --schema people \
  --save-history
```

### Large History File

History keeps last 100 entries automatically. To reset:

```bash
rm validation_history.json
```

### Report Not Opening

Ensure HTML file has `.html` extension:

```bash
--output report.html  # Not report.txt
```
