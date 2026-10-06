"""Machine-checkable registry of JSON entity types: schema module + output location +
whether the entity has a read-then-rewrite schema-contract (SCHEMA_TARGET) module.

This is the code form of docs/current/SCHEMA_VERSIONING.md — tests consume it so the
versioning list cannot silently drift from reality: a new entity type (or a schema change
that breaks real records) is caught by tests/test_entity_schema_consistency.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class EntitySpec:
    name: str
    schema_import: str  # "module:ATTR"
    output_glob: str  # relative to repo root
    contract_module: Optional[str]  # module declaring SCHEMA_TARGET, or None
    required_id: Optional[str]  # the primary-key field a real record must carry


ENTITY_REGISTRY = [
    EntitySpec(
        "events",
        "src.schemas.events_output:EVENTS_OUTPUT_SCHEMA",
        "output/content/*/*-event.json",
        None,
        None,
    ),
    EntitySpec(
        "dates",
        "src.schemas.dates_output:DATES_OUTPUT_SCHEMA",
        "output/dates/*.json",
        "src.extraction.dates",
        "DateID",
    ),
    EntitySpec(
        "places",
        "src.schemas.places_output:PLACES_OUTPUT_SCHEMA",
        "output/places/*.json",
        "src.extraction.places",
        "PlaceID",
    ),
    EntitySpec(
        "people",
        "src.schemas.people_output:PEOPLE_OUTPUT_SCHEMA",
        "output/people/*.json",
        "src.extraction.people",
        "PersonID",
    ),
    EntitySpec(
        "people_groups",
        "src.schemas.groups_output:GROUPS_OUTPUT_SCHEMA",
        "output/people_groups/*.json",
        "src.extraction.people_groups",
        "GroupID",
    ),
    EntitySpec(
        "equipment",
        "src.schemas.equipment_output:EQUIPMENT_OUTPUT_SCHEMA",
        "output/equipment/*.json",
        "src.extraction.equipment",
        "EquipmentID",
    ),
    EntitySpec(
        "weather",
        "src.schemas.weather_output:WEATHER_OUTPUT_SCHEMA",
        "output/weather/*.json",
        "src.extraction.weather_central",
        "WeatherID",
    ),
    EntitySpec(
        "logistics",
        "src.schemas.logistics_output:LOGISTICS_OUTPUT_SCHEMA",
        "output/logistics/*.json",
        None,
        "LogisticsID",
    ),
    EntitySpec(
        "casualties",
        "src.schemas.casualties_output:CASUALTIES_OUTPUT_SCHEMA",
        "output/casualties/*.json",
        None,
        "CasualtyID",
    ),
    EntitySpec(
        "maps",
        "src.schemas.maps_output:MAPS_OUTPUT_SCHEMA",
        "output/maps/*.json",
        None,
        "MapID",
    ),
    EntitySpec(
        "map_features",
        "src.schemas.map_features_output:MAP_FEATURES_OUTPUT_SCHEMA",
        "output/map_features/*.json",
        None,
        None,
    ),
    EntitySpec(
        "bibliography",
        "src.schemas.bibliography_output:BIBLIOGRAPHY_OUTPUT_SCHEMA",
        "output/bibliography/*.json",
        None,
        "BibliographyID",
    ),
]

# Known gap: 'images' (output/images/*.json) has NO enforced output schema yet
# (only extraction-time json_schemas.IMAGES_SCHEMA). Tracked in SCHEMA_VERSIONING.md.
UNENFORCED_ENTITIES = ["images"]


def load_schema(spec: EntitySpec) -> Any:
    """Import and return the enforced schema dict for a spec."""
    import importlib

    mod_name, attr = spec.schema_import.split(":")
    return getattr(importlib.import_module(mod_name), attr)
