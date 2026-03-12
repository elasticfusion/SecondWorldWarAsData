"""Unit tests for JSON validation utilities."""

import json
import tempfile
from pathlib import Path

import pytest
import ulid

from src.json_schemas import (
    CASUALTIES_SCHEMA,
    EQUIPMENT_SCHEMA,
    EVENT_SCHEMA,
    MAP_SCHEMA,
    PEOPLE_GROUPS_SCHEMA,
    PEOPLE_SCHEMA,
)
from src.utils.json_validator import validate_and_write_json, validate_json


class TestValidateJson:
    """Tests for validate_json function."""

    def test_valid_people_data(self):
        """Test validation with valid people data."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }
        assert validate_json(data, PEOPLE_SCHEMA) is True

    def test_invalid_people_data_missing_required(self):
        """Test validation fails with missing required field."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    # Missing 'name' field
                    "events": [],
                }
            ]
        }
        assert validate_json(data, PEOPLE_SCHEMA) is False

    def test_invalid_people_data_bad_ulid(self):
        """Test validation fails with invalid ULID."""
        data = {
            "people": [
                {
                    "PersonID": "invalid-ulid",
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }
        assert validate_json(data, PEOPLE_SCHEMA) is False

    def test_valid_equipment_data(self):
        """Test validation with valid equipment data."""
        data = {
            "equipment": [
                {
                    "EquipmentID": str(ulid.new()),
                    "name": "M4 Sherman",
                    "equipment_type": "tank",
                    "events": [],
                }
            ]
        }
        assert validate_json(data, EQUIPMENT_SCHEMA) is True

    def test_valid_map_data(self):
        """Test validation with valid map data."""
        data = {
            "MapID": str(ulid.new()),
            "title": "Test Map",
            "source": "Test Source",
        }
        assert validate_json(data, MAP_SCHEMA) is True

    def test_valid_casualty_data(self):
        """Test validation with valid casualty data."""
        data = {
            "casualties": [
                {
                    "CasualtyID": str(ulid.new()),
                    "type": "killed",
                    "EventID": str(ulid.new()),
                    "Sub-eventID": str(ulid.new()),
                }
            ]
        }
        assert validate_json(data, CASUALTIES_SCHEMA) is True

    def test_invalid_casualty_type(self):
        """Test validation fails with invalid casualty type."""
        data = {
            "casualties": [
                {
                    "CasualtyID": str(ulid.new()),
                    "type": "invalid_type",  # Not in enum
                    "EventID": str(ulid.new()),
                    "Sub-eventID": str(ulid.new()),
                }
            ]
        }
        assert validate_json(data, CASUALTIES_SCHEMA) is False


class TestValidateAndWriteJson:
    """Tests for validate_and_write_json function."""

    def test_write_valid_data_without_lock(self):
        """Test writing valid data without file locking."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

            assert filepath.exists()
            with open(filepath, encoding="utf-8") as f:
                written_data = json.load(f)
            assert written_data == data

    def test_write_valid_data_with_lock(self):
        """Test writing valid data with file locking."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=True)

            assert filepath.exists()
            with open(filepath, encoding="utf-8") as f:
                written_data = json.load(f)
            assert written_data == data

    def test_write_invalid_data_raises_error(self):
        """Test writing invalid data raises ValidationError."""
        from jsonschema import ValidationError

        data = {
            "PersonID": "invalid-ulid",
            "name": "Test Person",
            "events": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            with pytest.raises(ValidationError):
                validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

            # File should not be created
            assert not filepath.exists()

    def test_write_without_schema(self):
        """Test writing without schema validation."""
        data = {"any": "data", "no": "validation"}

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            validate_and_write_json(filepath, data, schema=None, use_lock=False)

            assert filepath.exists()
            with open(filepath) as f:
                written_data = json.load(f)
            assert written_data == data

    def test_creates_parent_directories(self):
        """Test that parent directories are created if they don't exist."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "subdir" / "nested" / "test.json"
            validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

            assert filepath.exists()
            assert filepath.parent.exists()

    def test_json_formatting(self):
        """Test that JSON is formatted correctly."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            validate_and_write_json(filepath, data, PEOPLE_SCHEMA, use_lock=False)

            # Read raw content
            content = filepath.read_text()

            # Check formatting
            assert "  " in content  # indent=2
            assert '"PersonID"' in content  # ensure_ascii=False allows quotes


class TestSchemaIntegrity:
    """Tests for schema definitions."""

    def test_all_schemas_are_dicts(self):
        """Test that all schemas are dictionaries."""
        schemas = [
            EVENT_SCHEMA,
            PEOPLE_SCHEMA,
            PEOPLE_GROUPS_SCHEMA,
            EQUIPMENT_SCHEMA,
            MAP_SCHEMA,
            CASUALTIES_SCHEMA,
        ]
        for schema in schemas:
            assert isinstance(schema, dict)

    def test_all_schemas_have_type(self):
        """Test that all schemas have 'type' key."""
        schemas = [
            EVENT_SCHEMA,
            PEOPLE_SCHEMA,
            PEOPLE_GROUPS_SCHEMA,
            EQUIPMENT_SCHEMA,
            MAP_SCHEMA,
            CASUALTIES_SCHEMA,
        ]
        for schema in schemas:
            assert "type" in schema

    def test_all_schemas_have_properties(self):
        """Test that all schemas have 'properties' key."""
        schemas = [
            EVENT_SCHEMA,
            PEOPLE_SCHEMA,
            PEOPLE_GROUPS_SCHEMA,
            EQUIPMENT_SCHEMA,
            MAP_SCHEMA,
            CASUALTIES_SCHEMA,
        ]
        for schema in schemas:
            assert "properties" in schema


class TestComplexValidation:
    """Tests for complex validation scenarios."""

    def test_people_with_events(self):
        """Test validation with nested event data."""
        data = {
            "people": [
                {
                    "PersonID": str(ulid.new()),
                    "name": "Test Person",
                    "events": [
                        {
                            "EventID": str(ulid.new()),
                            "Sub-eventID": str(ulid.new()),
                            "Event_Name": "Test Event",
                            "Sub-event_Name": "Test Sub-event",
                        }
                    ],
                }
            ]
        }
        assert validate_json(data, PEOPLE_SCHEMA) is True

    def test_people_groups_with_events(self):
        """Test validation with people groups."""
        data = {
            "groups": [
                {
                    "GroupID": str(ulid.new()),
                    "name": "Test Unit",
                    "group_type": "military_unit",
                    "events": [],
                }
            ]
        }
        assert validate_json(data, PEOPLE_GROUPS_SCHEMA) is True

    def test_equipment_with_specifications(self):
        """Test validation with equipment specifications."""
        data = {
            "equipment": [
                {
                    "EquipmentID": str(ulid.new()),
                    "name": "M4 Sherman",
                    "equipment_type": "tank",
                    "specifications": {"weight": "30 tons", "crew": 5},
                    "events": [],
                }
            ]
        }
        assert validate_json(data, EQUIPMENT_SCHEMA) is True
