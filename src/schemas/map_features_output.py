"""Strict output schema for map-interior feature files (output/map_features/*.json).

A GeoJSON FeatureCollection carrying the structured interior of a historical map
(places, unit positions, fortifications, routes) extracted by Grok vision, with
entity-graph links (PlaceID/GroupID/DateID) + provenance. Adapts GeoJSON + the
TacticalJSON/APP-6 profile (see docs/current/features/maps/MAP_FEATURES_SCHEMA.md).

Coordinates come from the resolved PlaceID (geocode cascade), NEVER map pixels; so
`geometry` is null until geocoded and `coordinate_source` is never "map_pixels".
"""

from src.schemas import (
    METADATA_PROPERTIES,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
)

_LEGEND_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "symbol_description": make_nullable("string"),
        "meaning": make_nullable("string"),
        "meaning_en": make_nullable("string"),
        "date_text": make_nullable("string"),
        "DateID": ulid_field(nullable=True),
        "confidence": make_nullable("number"),
    },
}

_GEOMETRY = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "type": enum_field(["Point", "LineString", "Polygon"]),
        # GeoJSON coordinates: Point=[lon,lat]; Line/Polygon=nested arrays.
        "coordinates": {"type": "array"},
    },
}

_FEATURE_PROPERTIES = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "feature_kind": enum_field(
            [
                "place",
                "unit_position",
                "unit_boundary",
                "route",
                "fortification",
                "river",
                "road",
                "railroad",
                "elevation",
                "front_line",
            ]
        ),
        # Provenance (source is authority; verbatim label always kept)
        "original_label": make_nullable("string"),
        "source": enum_field(["map"]),
        "confidence": make_nullable("number"),
        "legend_key": make_nullable("string"),
        "tile_id": make_nullable("string"),
        "coordinate_source": enum_field(
            ["placeid_gazetteer", "anchor_place", "none"], nullable=True
        ),
        # Entity-graph links (null when unresolved; never guessed)
        "PlaceID": ulid_field(nullable=True),
        "GroupID": ulid_field(nullable=True),
        "PersonID": ulid_field(nullable=True),
        "DateID": ulid_field(nullable=True),
        # 2525/APP-6 modifiers + tactical semantics (symbology-derived)
        "sidc": make_nullable("string"),
        "uniqueDesignation": make_nullable("string"),
        "higherFormation": make_nullable("string"),
        "additionalInformation": make_nullable("string"),
        "dtg": make_nullable("string"),
        "affiliation": enum_field(
            ["friend", "hostile", "neutral", "unknown"], nullable=True
        ),
        "echelon": make_nullable("string"),
        "branch": make_nullable("string"),
        "posture": enum_field(["attack", "defend", "axis"], nullable=True),
    },
    "required": ["feature_kind", "source"],
}

_FEATURE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": enum_field(["Feature"]),
        "geometry": _GEOMETRY,
        "properties": _FEATURE_PROPERTIES,
    },
    "required": ["type", "geometry", "properties"],
}

MAP_FEATURES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("map_features"),
    "title": "Map Features Output File",
    "type": "object",
    "required": ["type", "features"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "type": enum_field(["FeatureCollection"]),
        "MapID": ulid_field(nullable=True),
        "map_number": make_nullable("string"),
        "map_title": make_nullable("string"),
        "title_en": make_nullable("string"),
        "legend": {"type": ["array", "null"], "items": _LEGEND_ITEM},
        "elevation_scale": make_nullable("string"),
        "distance_scale": make_nullable("string"),
        # Reverse-link coverage extent (populated by the extent pass; optional for now)
        "covered_places": {
            "type": ["array", "null"],
            "items": ulid_field(),
        },
        "date_range": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "earliest": make_nullable("string"),
                "latest": make_nullable("string"),
            },
        },
        "features": {"type": "array", "items": _FEATURE},
        # Prototype diagnostics (optional; not persisted in production records)
        "resolution_report": {"type": ["object", "null"]},
    },
}
