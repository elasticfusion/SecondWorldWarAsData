"""Schema registry for the READ/REPORT tooling (validate_data / validation_report / dashboard).

SINGLE SOURCE OF TRUTH: this now serves the SAME schemas the write guard enforces — the
``*_OUTPUT_SCHEMA`` dicts resolved via ``src.schemas.entity_registry`` — NOT the stale
``src.json_schemas`` models (those described the pre-refactor event-nested shape, e.g. a ``people``
wrapper / top-level ``EventID`` on places, which current per-entity-file records do not match, so
the read tooling was reporting against a schema the corpus no longer uses). Read-time and write-time
validation are therefore provably consistent: a record the write guard accepts is the same record
the report tooling accepts.

The historical read-tool schema NAMES (singular ``event``/``place``/``date``/``map``) are kept as
aliases of the registry entity names so existing CLIs/tests keep working.
"""

from typing import Any, Dict, Optional

from jsonschema import Draft7Validator

# Read-tool schema name -> registry entity name (singular legacy aliases + identity mappings).
_NAME_TO_ENTITY = {
    "event": "events",
    "events": "events",
    "date": "dates",
    "dates": "dates",
    "place": "places",
    "places": "places",
    "people": "people",
    "people_groups": "people_groups",
    "groups": "people_groups",
    "equipment": "equipment",
    "map": "maps",
    "maps": "maps",
    "map_features": "map_features",
    "casualties": "casualties",
    "weather": "weather",
    "logistics": "logistics",
    "images": "images",
    "bibliography": "bibliography",
    "source_section": "source_section",
}


def _load_authoritative_schemas() -> Dict[str, Any]:
    """Build {read-tool-name: schema} from the authoritative registry (the write-guard source)."""
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    by_entity: Dict[str, Any] = {}
    for spec in ENTITY_REGISTRY:
        try:
            by_entity[spec.name] = load_schema(spec)
        except (
            Exception
        ):  # noqa: BLE001 - a missing schema module must not break the whole map
            continue
    # Expose under every known read-tool alias that resolves to a loaded entity.
    return {
        name: by_entity[entity]
        for name, entity in _NAME_TO_ENTITY.items()
        if entity in by_entity
    }


class SchemaRegistry:
    """Lazy-loads + caches validators for the AUTHORITATIVE (write-guard) schemas."""

    def __init__(self):
        self._validators: Dict[str, Draft7Validator] = {}
        self._schema_map = _load_authoritative_schemas()

    def get_validator(self, schema_name: str) -> Optional[Draft7Validator]:
        """Get the cached validator for a schema (compiled from the authoritative source)."""
        if schema_name not in self._validators:
            schema = self._schema_map.get(schema_name)
            if schema:
                self._validators[schema_name] = Draft7Validator(schema)
        return self._validators.get(schema_name)

    def get_schema(self, schema_name: str) -> Optional[Dict[str, Any]]:
        """Get the raw authoritative schema by name (or alias)."""
        return self._schema_map.get(schema_name)

    def list_schemas(self) -> list:
        """List CANONICAL schema names (registry entity names), de-duplicated — NOT the singular
        legacy aliases. Callers that ITERATE (e.g. the dashboard scanning ``output/<name>/``) get
        each real entity dir once with no phantom singular dirs; aliases (``event``/``place``/…)
        remain resolvable via ``get_schema``/``get_validator``."""
        canonical: list = []
        seen = set()
        for name, entity in _NAME_TO_ENTITY.items():
            if entity in self._schema_map and entity not in seen:
                # prefer the canonical (plural/registry) name, which is always a key in the map
                canonical.append(entity)
                seen.add(entity)
        return canonical


# Global registry instance
_registry = SchemaRegistry()


def get_registry() -> SchemaRegistry:
    """Get the global schema registry instance."""
    return _registry
