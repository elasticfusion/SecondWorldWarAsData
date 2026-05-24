"""Integration tests for JSON validation system."""

import json
import tempfile
from pathlib import Path

import pytest

from src.json_schemas import (
    CASUALTIES_SCHEMA,
    DATE_SCHEMA,
    EQUIPMENT_SCHEMA,
    EVENT_SCHEMA,
    MAP_SCHEMA,
    PEOPLE_GROUPS_SCHEMA,
    PEOPLE_SCHEMA,
    PLACE_SCHEMA,
    SUPPLEMENTAL_SCHEMA,
)
from src.utils.json_validator import (
    get_validation_stats,
    register_post_validation_hook,
    register_pre_validation_hook,
    validate_and_write_json,
    validate_directory,
    validate_json,
)
from src.utils.schema_evolution import (
    detect_schema_version,
    generate_migration_report,
    migrate_data,
    migrate_file,
    register_migration,
    scan_versions,
)
from src.utils.schema_registry import get_registry

# Valid ULIDs for testing
ULID1 = "01HQXYZ123456789ABCDEFGHJK"
ULID2 = "01HQXYZ123456789ABCDEFGHJM"
ULID3 = "01HQXYZ123456789ABCDEFGHJN"
ULID4 = "01HQXYZ123456789ABCDEFGHJP"


class TestEndToEndValidation:
    """Test complete validation workflow."""

    def test_write_and_validate_people(self, tmp_path):
        """Test writing and validating people data."""
        data = {
            "people": [
                {
                    "PersonID": ULID1,
                    "name": "John Doe",
                }
            ]
        }

        filepath = tmp_path / "person.json"
        validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

        assert filepath.exists()
        loaded = json.loads(filepath.read_text())
        assert loaded["people"][0]["PersonID"] == ULID1
        assert validate_json(loaded, PEOPLE_SCHEMA)

    def test_write_and_validate_event(self, tmp_path):
        """Test writing and validating event data."""
        data = {
            "Chapter": "Test Chapter",
            "Event": {
                "EventID": ULID1,
                "Sub-events": [
                    {
                        "Sub-eventID": ULID2,
                        "Sub-event_summary": "Test",
                        "Sub-event_fulltext": {},
                    }
                ],
            },
        }

        filepath = tmp_path / "event.json"
        validate_and_write_json(filepath, data, EVENT_SCHEMA, use_lock=False)

        assert filepath.exists()
        loaded = json.loads(filepath.read_text())
        assert validate_json(loaded, EVENT_SCHEMA)


class TestBatchValidation:
    """Test batch validation of directories."""

    def test_validate_directory_all_valid(self, tmp_path):
        """Test validating directory with all valid files."""
        # Create test files
        for i in range(5):
            data = {
                "people": [
                    {
                        "PersonID": f"01HQXYZ12345678{i}ABCDEFGHJK",
                        "name": f"Person {i}",
                    }
                ]
            }
            filepath = tmp_path / f"person_{i}.json"
            filepath.write_text(json.dumps(data))

        results = validate_directory(tmp_path, PEOPLE_SCHEMA)

        assert results["total"] == 5
        assert results["valid"] == 5
        assert results["invalid"] == 0
        assert len(results["errors"]) == 0

    def test_validate_directory_with_invalid(self, tmp_path):
        """Test validating directory with some invalid files."""
        # Valid file
        valid_data = {
            "people": [
                {
                    "PersonID": ULID1,
                    "name": "Valid Person",
                }
            ]
        }
        (tmp_path / "valid.json").write_text(json.dumps(valid_data))

        # Invalid file (missing required field)
        invalid_data = {"PersonID": ULID2}
        (tmp_path / "invalid.json").write_text(json.dumps(invalid_data))

        results = validate_directory(tmp_path, PEOPLE_SCHEMA)

        assert results["total"] == 2
        assert results["valid"] == 1
        assert results["invalid"] == 1
        assert len(results["errors"]) == 1


class TestSchemaRegistry:
    """Test schema registry integration."""

    def test_registry_has_all_schemas(self):
        """Test registry contains all schemas."""
        registry = get_registry()
        schemas = registry.list_schemas()

        expected = [
            "event",
            "date",
            "place",
            "supplemental",
            "people",
            "people_groups",
            "equipment",
            "map",
            "casualties",
        ]

        for schema_name in expected:
            assert schema_name in schemas

    def test_get_validator_caching(self):
        """Test validator caching works."""
        registry = get_registry()

        validator1 = registry.get_validator("people")
        validator2 = registry.get_validator("people")

        # Should return same cached instance
        assert validator1 is validator2

    def test_validate_with_registry(self, tmp_path):
        """Test validation using registry."""
        registry = get_registry()
        schema = registry.get_schema("people")

        data = {
            "people": [
                {
                    "PersonID": ULID1,
                    "name": "Test Person",
                }
            ]
        }

        assert validate_json(data, schema)


class TestValidationHooks:
    """Test validation hooks integration."""

    def test_pre_validation_hook(self):
        """Test pre-validation hook is called."""
        hook_called = []

        def test_hook(data):
            hook_called.append(data)

        register_pre_validation_hook(test_hook)

        data = {"people": [{"PersonID": ULID1, "name": "Test"}]}

        validate_json(data, PEOPLE_SCHEMA)
        assert len(hook_called) > 0

    def test_post_validation_hook(self):
        """Test post-validation hook is called."""
        hook_results = []

        def test_hook(data, is_valid):
            hook_results.append(is_valid)

        register_post_validation_hook(test_hook)

        # Valid data
        valid_data = {"people": [{"PersonID": ULID1, "name": "Test"}]}
        validate_json(valid_data, PEOPLE_SCHEMA)

        # Invalid data
        invalid_data = {"PersonID": "invalid"}
        validate_json(invalid_data, PEOPLE_SCHEMA)

        assert True in hook_results  # At least one valid
        assert False in hook_results  # At least one invalid


class TestSchemaEvolution:
    """Test schema evolution and migration."""

    def test_detect_version_from_file(self, tmp_path):
        """Test version detection from file."""
        data = {"version": "1.0", "name": "test"}
        filepath = tmp_path / "test.json"
        filepath.write_text(json.dumps(data))

        version = detect_schema_version(filepath)
        assert version == "1.0"

    def test_migration_workflow(self, tmp_path):
        """Test complete migration workflow."""

        # Register test migration
        @register_migration("test_schema", "1.0", "1.1")
        def migrate_test(data):
            data = data.copy()
            data["new_field"] = "added"
            return data

        # Create v1.0 file
        data_v1 = {"version": "1.0", "name": "test"}
        filepath = tmp_path / "test.json"
        filepath.write_text(json.dumps(data_v1))

        # Migrate
        success = migrate_file(filepath, "test_schema", "1.1", backup=True)
        assert success

        # Verify migration
        migrated = json.loads(filepath.read_text())
        assert migrated["version"] == "1.1"
        assert migrated["new_field"] == "added"

        # Verify backup exists
        backup = tmp_path / "test.1.0.bak"
        assert backup.exists()

    def test_scan_versions(self, tmp_path):
        """Test version scanning."""
        # Create files with different versions
        for i, version in enumerate(["1.0", "1.0", "1.1", "1.1", "1.1"]):
            data = {"version": version, "name": f"test_{i}"}
            (tmp_path / f"file_{i}.json").write_text(json.dumps(data))

        versions = scan_versions(tmp_path)

        assert versions["1.0"] == 2
        assert versions["1.1"] == 3

    def test_migration_report(self, tmp_path):
        """Test migration report generation."""
        # Create test files
        for i in range(3):
            data = {"version": "1.0", "name": f"test_{i}"}
            (tmp_path / f"file_{i}.json").write_text(json.dumps(data))

        report = generate_migration_report(tmp_path, "test_schema")

        assert "test_schema" in report
        assert "1.0: 3 files" in report


class TestPerformanceMetrics:
    """Test validation performance tracking."""

    def test_validation_stats_tracking(self, tmp_path):
        """Test validation statistics are tracked."""
        # Perform validation with write (which tracks stats)
        data = {"people": [{"PersonID": ULID1, "name": "Test"}]}
        filepath = tmp_path / "test.json"

        # Get initial stats
        initial_stats = get_validation_stats()
        initial_total = initial_stats.get("total", 0)

        # Validate and write (this increments stats)
        validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

        # Check stats updated
        new_stats = get_validation_stats()
        assert new_stats["total"] > initial_total


class TestAllSchemas:
    """Test all schemas with valid data."""

    def test_event_schema(self):
        """Test EVENT_SCHEMA validation."""
        data = {
            "Chapter": "Test",
            "Event": {
                "EventID": ULID1,
                "Sub-events": [
                    {
                        "Sub-eventID": ULID2,
                        "Sub-event_summary": "Test",
                        "Sub-event_fulltext": {},
                    }
                ],
            },
        }
        assert validate_json(data, EVENT_SCHEMA)

    def test_date_schema(self):
        """Test DATE_SCHEMA validation."""
        data = {
            "Event_Name": "Test Event",
            "EventID": ULID1,
            "Sub-event_Name": "Test Sub-event",
            "Sub-eventID": ULID2,
            "Date_Mentions": [
                {
                    "DateMentionID": ULID3,
                    "date_start": "1939-09-01",
                    "original_text": "September 1, 1939",
                }
            ],
        }
        assert validate_json(data, DATE_SCHEMA)

    def test_equipment_schema(self):
        """Test EQUIPMENT_SCHEMA validation."""
        data = {
            "equipment": [
                {
                    "EquipmentID": ULID1,
                    "name": "M4 Sherman",
                }
            ]
        }
        assert validate_json(data, EQUIPMENT_SCHEMA)

    def test_map_schema(self):
        """Test MAP_SCHEMA validation."""
        data = {
            "MapID": ULID1,
            "map_title": "Test Map",
            "source_book": "Test Book",
            "source_author": "Test Author",
            "EventID": ULID2,
            "Sub_eventID": ULID3,
            "local_path": "output/maps/test.json",
            "extracted_date": "2026-04-19T00:00:00Z",
        }
        assert validate_json(data, MAP_SCHEMA)

    def test_casualties_schema(self):
        """Test CASUALTIES_SCHEMA validation."""
        data = {
            "casualties": [
                {
                    "CasualtyID": ULID1,
                    "type": "killed",
                    "description": "Test casualty",
                    "event_context": {"EventID": ULID2},
                    "source": {"book": "Test Book", "chapter": "1"},
                }
            ]
        }
        assert validate_json(data, CASUALTIES_SCHEMA)
