"""Tests for JSON schema validity."""

import pytest
from jsonschema import Draft7Validator, SchemaError

from src.json_schemas import (
    CASUALTIES_SCHEMA,
    DATE_SCHEMA,
    EQUIPMENT_SCHEMA,
    EVENT_SCHEMA,
    MAP_SCHEMA,
    PEOPLE_GROUPS_SCHEMA,
    PEOPLE_SCHEMA,
    PLACE_SCHEMA,
    SCHEMA_VERSION,
    SUPPLEMENTAL_SCHEMA,
)


class TestSchemaValidity:
    """Test that all schemas are valid JSON Schema."""

    @pytest.mark.parametrize(
        "schema_name,schema",
        [
            ("EVENT_SCHEMA", EVENT_SCHEMA),
            ("DATE_SCHEMA", DATE_SCHEMA),
            ("PLACE_SCHEMA", PLACE_SCHEMA),
            ("SUPPLEMENTAL_SCHEMA", SUPPLEMENTAL_SCHEMA),
            ("PEOPLE_SCHEMA", PEOPLE_SCHEMA),
            ("PEOPLE_GROUPS_SCHEMA", PEOPLE_GROUPS_SCHEMA),
            ("EQUIPMENT_SCHEMA", EQUIPMENT_SCHEMA),
            ("MAP_SCHEMA", MAP_SCHEMA),
            ("CASUALTIES_SCHEMA", CASUALTIES_SCHEMA),
        ],
    )
    def test_schema_is_valid(self, schema_name, schema):
        """Test that schema is valid JSON Schema Draft 7."""
        try:
            Draft7Validator.check_schema(schema)
        except SchemaError as e:
            pytest.fail(f"{schema_name} is not valid JSON Schema: {e}")

    @pytest.mark.parametrize(
        "schema_name,schema",
        [
            ("EVENT_SCHEMA", EVENT_SCHEMA),
            ("DATE_SCHEMA", DATE_SCHEMA),
            ("PLACE_SCHEMA", PLACE_SCHEMA),
            ("SUPPLEMENTAL_SCHEMA", SUPPLEMENTAL_SCHEMA),
            ("PEOPLE_SCHEMA", PEOPLE_SCHEMA),
            ("PEOPLE_GROUPS_SCHEMA", PEOPLE_GROUPS_SCHEMA),
            ("EQUIPMENT_SCHEMA", EQUIPMENT_SCHEMA),
            ("MAP_SCHEMA", MAP_SCHEMA),
            ("CASUALTIES_SCHEMA", CASUALTIES_SCHEMA),
        ],
    )
    def test_schema_has_version(self, schema_name, schema):
        """Test that schema has version field."""
        assert "version" in schema, f"{schema_name} missing version field"
        assert schema["version"] == SCHEMA_VERSION

    @pytest.mark.parametrize(
        "schema_name,schema",
        [
            ("EVENT_SCHEMA", EVENT_SCHEMA),
            ("DATE_SCHEMA", DATE_SCHEMA),
            ("PLACE_SCHEMA", PLACE_SCHEMA),
            ("SUPPLEMENTAL_SCHEMA", SUPPLEMENTAL_SCHEMA),
            ("PEOPLE_SCHEMA", PEOPLE_SCHEMA),
            ("PEOPLE_GROUPS_SCHEMA", PEOPLE_GROUPS_SCHEMA),
            ("EQUIPMENT_SCHEMA", EQUIPMENT_SCHEMA),
            ("MAP_SCHEMA", MAP_SCHEMA),
            ("CASUALTIES_SCHEMA", CASUALTIES_SCHEMA),
        ],
    )
    def test_schema_has_draft_declaration(self, schema_name, schema):
        """Test that schema declares JSON Schema draft version."""
        assert "$schema" in schema, f"{schema_name} missing $schema declaration"
        assert "draft-07" in schema["$schema"]

    @pytest.mark.parametrize(
        "schema_name,schema",
        [
            ("EVENT_SCHEMA", EVENT_SCHEMA),
            ("DATE_SCHEMA", DATE_SCHEMA),
            ("PLACE_SCHEMA", PLACE_SCHEMA),
            ("SUPPLEMENTAL_SCHEMA", SUPPLEMENTAL_SCHEMA),
            ("PEOPLE_SCHEMA", PEOPLE_SCHEMA),
            ("PEOPLE_GROUPS_SCHEMA", PEOPLE_GROUPS_SCHEMA),
            ("EQUIPMENT_SCHEMA", EQUIPMENT_SCHEMA),
            ("MAP_SCHEMA", MAP_SCHEMA),
            ("CASUALTIES_SCHEMA", CASUALTIES_SCHEMA),
        ],
    )
    def test_schema_has_type(self, schema_name, schema):
        """Test that schema has type field."""
        assert "type" in schema, f"{schema_name} missing type field"
        assert schema["type"] == "object"

    @pytest.mark.parametrize(
        "schema_name,schema",
        [
            ("EVENT_SCHEMA", EVENT_SCHEMA),
            ("DATE_SCHEMA", DATE_SCHEMA),
            ("PLACE_SCHEMA", PLACE_SCHEMA),
            ("SUPPLEMENTAL_SCHEMA", SUPPLEMENTAL_SCHEMA),
            ("PEOPLE_SCHEMA", PEOPLE_SCHEMA),
            ("PEOPLE_GROUPS_SCHEMA", PEOPLE_GROUPS_SCHEMA),
            ("EQUIPMENT_SCHEMA", EQUIPMENT_SCHEMA),
            ("MAP_SCHEMA", MAP_SCHEMA),
            ("CASUALTIES_SCHEMA", CASUALTIES_SCHEMA),
        ],
    )
    def test_schema_has_properties(self, schema_name, schema):
        """Test that schema has properties."""
        assert "properties" in schema, f"{schema_name} missing properties"
        assert len(schema["properties"]) > 0, f"{schema_name} has no properties"


class TestSchemaULIDPatterns:  # pylint: disable=too-few-public-methods
    """Test ULID patterns in schemas."""

    ULID_PATTERN = "^[0-9A-HJKMNP-TV-Z]{26}$"

    def test_ulid_pattern_consistency(self):
        """Test that all ULID patterns are consistent."""
        schemas = [
            EVENT_SCHEMA,
            DATE_SCHEMA,
            PLACE_SCHEMA,
            PEOPLE_SCHEMA,
            PEOPLE_GROUPS_SCHEMA,
            EQUIPMENT_SCHEMA,
            MAP_SCHEMA,
            CASUALTIES_SCHEMA,
        ]

        for schema in schemas:
            self._check_ulid_patterns(schema)

    def _check_ulid_patterns(self, obj, path=""):
        """Recursively check ULID patterns."""
        if isinstance(obj, dict):
            # Check if this is a ULID field
            if "pattern" in obj and "ID" in path:
                assert (
                    obj["pattern"] == self.ULID_PATTERN
                ), f"Inconsistent ULID pattern at {path}: {obj['pattern']}"

            # Recurse
            for key, value in obj.items():
                self._check_ulid_patterns(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._check_ulid_patterns(item, f"{path}[{i}]")


class TestSchemaRequiredFields:
    """Test required fields in schemas."""

    def test_all_schemas_have_required_fields(self):
        """Test that schemas with required fields declare them."""
        schemas = {
            "EVENT": EVENT_SCHEMA,
            "DATE": DATE_SCHEMA,
            "PLACE": PLACE_SCHEMA,
            "SUPPLEMENTAL": SUPPLEMENTAL_SCHEMA,
            "PEOPLE": PEOPLE_SCHEMA,
            "PEOPLE_GROUPS": PEOPLE_GROUPS_SCHEMA,
            "EQUIPMENT": EQUIPMENT_SCHEMA,
            "MAP": MAP_SCHEMA,
            "CASUALTIES": CASUALTIES_SCHEMA,
        }

        for name, schema in schemas.items():
            assert "required" in schema, f"{name} schema missing required field"
            assert isinstance(
                schema["required"], list
            ), f"{name} required must be a list"
            assert len(schema["required"]) > 0, f"{name} has no required fields"

    def test_required_fields_exist_in_properties(self):
        """Test that required fields are defined in properties."""
        schemas = {
            "EVENT": EVENT_SCHEMA,
            "DATE": DATE_SCHEMA,
            "PLACE": PLACE_SCHEMA,
            "SUPPLEMENTAL": SUPPLEMENTAL_SCHEMA,
            "PEOPLE": PEOPLE_SCHEMA,
            "PEOPLE_GROUPS": PEOPLE_GROUPS_SCHEMA,
            "EQUIPMENT": EQUIPMENT_SCHEMA,
            "MAP": MAP_SCHEMA,
            "CASUALTIES": CASUALTIES_SCHEMA,
        }

        for name, schema in schemas.items():
            required = schema.get("required", [])
            properties = schema.get("properties", {})

            for field in required:
                assert (
                    field in properties
                ), f"{name}: required field '{field}' not in properties"
