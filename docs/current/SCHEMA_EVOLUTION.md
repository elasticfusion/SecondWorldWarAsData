# Schema Evolution Tools

Tools for managing schema versions and migrations.

## Features

1. **Version Detection** - Automatically detect schema versions in JSON files
2. **Migration Registry** - Register and manage migration functions
3. **Batch Migration** - Migrate entire directories
4. **Version Scanning** - Analyze version distribution
5. **Automatic Backups** - Create backups before migration

## Quick Start

### 1. Scan Directory for Versions

```bash
python scripts/migrate_schema.py scan output/people
```

Output:
```
Version distribution in output/people:
  1.0: 150 files
  1.1: 25 files
  unknown: 5 files
```

### 2. Generate Migration Report

```bash
python scripts/migrate_schema.py report output/people --schema people
```

Output:
```
Migration Report: output/people
Schema: people
Total files: 180

Version distribution:
  1.0: 150 files (83.3%)
  1.1: 25 files (13.9%)
  unknown: 5 files (2.8%)
```

### 3. Migrate Files

```bash
# Migrate all files to version 1.1 (with backups)
python scripts/migrate_schema.py migrate output/people --schema people --to-version 1.1

# Migrate without backups
python scripts/migrate_schema.py migrate output/people --schema people --to-version 1.1 --no-backup

# Migrate specific pattern
python scripts/migrate_schema.py migrate output/people --schema people --to-version 1.1 --pattern "person_*.json"
```

## Creating Migrations

### Step 1: Register Migration Function

Create migration in `src/migrations/__init__.py`:

```python
from src.utils.schema_evolution import register_migration

@register_migration("people", "1.0", "1.1")
def migrate_people_1_0_to_1_1(data):
    """
    Migrate people schema from 1.0 to 1.1.
    
    Changes:
    - Add 'verified' field (default: False)
    - Rename 'biography' to 'bio'
    """
    migrated = data.copy()
    
    # Add new field
    if "verified" not in migrated:
        migrated["verified"] = False
    
    # Rename field
    if "biography" in migrated:
        migrated["bio"] = migrated.pop("biography")
    
    return migrated
```

### Step 2: Test Migration

```python
from src.utils.schema_evolution import migrate_data

data_v1 = {
    "version": "1.0",
    "PersonID": "01HQXYZ...",
    "name": "John Doe",
    "biography": "..."
}

migrated = migrate_data(data_v1, "people", "1.0", "1.1")
# Result: {"version": "1.1", "PersonID": "...", "name": "...", "bio": "...", "verified": False}
```

### Step 3: Run Migration

```bash
python scripts/migrate_schema.py migrate output/people --schema people --to-version 1.1
```

## Programmatic Usage

### Detect Version

```python
from pathlib import Path
from src.utils.schema_evolution import detect_schema_version

version = detect_schema_version(Path("output/people/person_123.json"))
print(f"Version: {version}")
```

### Migrate Single File

```python
from pathlib import Path
from src.utils.schema_evolution import migrate_file

success = migrate_file(
    Path("output/people/person_123.json"),
    schema_name="people",
    to_version="1.1",
    backup=True
)
```

### Scan Versions

```python
from pathlib import Path
from src.utils.schema_evolution import scan_versions

versions = scan_versions(Path("output/people"))
# Result: {"1.0": 150, "1.1": 25, "unknown": 5}
```

### Generate Report

```python
from pathlib import Path
from src.utils.schema_evolution import generate_migration_report

report = generate_migration_report(Path("output/people"), "people")
print(report)
```

## Migration Best Practices

### 1. Always Test First

```bash
# Test on a single file
cp output/people/person_123.json /tmp/test.json
python scripts/migrate_schema.py migrate /tmp --schema people --to-version 1.1
```

### 2. Use Backups

Backups are created automatically with `.{version}.bak` extension:
- `person_123.json` → `person_123.1.0.bak`

### 3. Incremental Migrations

For major version changes, create incremental migrations:

```python
@register_migration("people", "1.0", "1.1")
def migrate_1_0_to_1_1(data):
    # Small changes
    pass

@register_migration("people", "1.1", "2.0")
def migrate_1_1_to_2_0(data):
    # More changes
    pass
```

### 4. Document Changes

Always document what changed in the migration function docstring.

### 5. Handle Missing Fields

```python
@register_migration("people", "1.0", "1.1")
def migrate(data):
    migrated = data.copy()
    
    # Safe field access
    if "old_field" in migrated:
        migrated["new_field"] = migrated.pop("old_field")
    
    # Default values
    migrated.setdefault("new_field", "default")
    
    return migrated
```

## Version Format

Versions should be in data files:

```json
{
  "version": "1.0",
  "PersonID": "...",
  ...
}
```

Or:

```json
{
  "schema_version": "1.0",
  "PersonID": "...",
  ...
}
```

## Error Handling

The migration tool handles errors gracefully:

- Invalid JSON: Skipped with error message
- Missing migration: Clear error with available versions
- File I/O errors: Logged and counted

Exit codes:
- `0`: Success
- `1`: One or more errors occurred

## Examples

### Example 1: Add New Field

```python
@register_migration("equipment", "1.0", "1.1")
def add_category(data):
    data = data.copy()
    data["category"] = "unknown"
    return data
```

### Example 2: Restructure Data

```python
@register_migration("equipment", "1.1", "2.0")
def restructure_specs(data):
    data = data.copy()
    if "specifications" in data:
        data["specifications"] = {
            "items": data["specifications"],
            "last_updated": None
        }
    return data
```

### Example 3: Rename Field

```python
@register_migration("people", "1.0", "1.1")
def rename_biography(data):
    data = data.copy()
    if "biography" in data:
        data["bio"] = data.pop("biography")
    return data
```

## Integration with Validation

Migrations work seamlessly with validation:

```python
from src.utils.schema_evolution import migrate_data
from src.utils.json_validator import validate_json
from src.json_schemas import PEOPLE_SCHEMA

# Migrate
migrated = migrate_data(data, "people", "1.0", "1.1")

# Validate
if validate_json(migrated, PEOPLE_SCHEMA):
    print("Migration successful and valid!")
```
