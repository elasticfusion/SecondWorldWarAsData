"""Schema registry for lazy loading and caching validators."""

from typing import Any, Dict, Optional

from jsonschema import Draft7Validator

from src import json_schemas


class SchemaRegistry:
    """Manages schema loading and validator caching."""

    def __init__(self):
        """Initialize empty cache."""
        self._validators: Dict[str, Draft7Validator] = {}
        self._schema_map = {
            "event": json_schemas.EVENT_SCHEMA,
            "date": json_schemas.DATE_SCHEMA,
            "place": json_schemas.PLACE_SCHEMA,
            "supplemental": json_schemas.SUPPLEMENTAL_SCHEMA,
            "people": json_schemas.PEOPLE_SCHEMA,
            "people_groups": json_schemas.PEOPLE_GROUPS_SCHEMA,
            "equipment": json_schemas.EQUIPMENT_SCHEMA,
            "map": json_schemas.MAP_SCHEMA,
            "casualties": json_schemas.CASUALTIES_SCHEMA,
        }

    def get_validator(self, schema_name: str) -> Optional[Draft7Validator]:
        """
        Get cached validator for schema.

        Args:
            schema_name: Name of schema (e.g., 'people', 'event')

        Returns:
            Compiled validator or None if schema not found
        """
        if schema_name not in self._validators:
            schema = self._schema_map.get(schema_name)
            if schema:
                self._validators[schema_name] = Draft7Validator(schema)

        return self._validators.get(schema_name)

    def get_schema(self, schema_name: str) -> Optional[Dict[str, Any]]:
        """
        Get raw schema by name.

        Args:
            schema_name: Name of schema

        Returns:
            Schema dictionary or None if not found
        """
        return self._schema_map.get(schema_name)

    def list_schemas(self) -> list:
        """List all available schema names."""
        return list(self._schema_map.keys())


# Global registry instance
_registry = SchemaRegistry()


def get_registry() -> SchemaRegistry:
    """Get global schema registry instance."""
    return _registry
