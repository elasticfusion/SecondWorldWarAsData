"""Strict output schema for places files (output/places/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    QUALITY_FLAGS_PROPERTY,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
    url_field,
)

PLACES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("places"),
    "title": "Places Output File",
    "type": "object",
    "required": ["PlaceID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        **QUALITY_FLAGS_PROPERTY,
        "PlaceID": ulid_field(),
        "current_name": make_nullable("string"),
        "name": make_nullable("string"),
        "historical_names": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "source_language": make_nullable("string"),
        "geography_type": make_nullable("string"),
        # Top-level coordinates (geocode cascade also writes these at the root, not only
        # under the nested "coordinates" object).
        "latitude": {"type": ["number", "null"], "minimum": -90, "maximum": 90},
        "longitude": {"type": ["number", "null"], "minimum": -180, "maximum": 180},
        # Geocoding-enrichment outputs (places_grok_geocode cascade) + identity fields.
        "country": make_nullable("string"),
        "geocode_source": make_nullable("string"),
        "geo_review": make_nullable("string"),
        "place_name": make_nullable("string"),
        "identified_as": make_nullable("string"),
        "place_type": make_nullable("string"),
        "source": make_nullable("string"),
        # Phase-2 Grokipedia descriptive lookup (grokipedia_url set in extraction;
        # grokipedia_checked_at stamped even on a miss so it is not re-fetched).
        "grokipedia_url": make_nullable("string"),
        "grokipedia_checked_at": make_nullable("string"),
        # Phase-2 Wikipedia enrichment (url + image, flowing through the same path).
        # wikipedia_checked_at is stamped even on a miss so it is not re-fetched.
        "wikipedia_url": make_nullable("string"),
        "wikipedia_checked_at": make_nullable("string"),
        "images": {"type": ["array", "null"], "items": {"type": ["object", "string"]}},
        # OpenSERP (Phase 3) web/source results for the place + gate.
        "web_results": {
            "type": ["array", "null"],
            "items": {"type": "object", "additionalProperties": True},
        },
        "openserp_searched": {"type": ["boolean", "null"]},
        "openserp_searched_at": {"type": ["number", "null"]},
        "coordinates": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                "precision": enum_field(
                    ["exact", "approximate", "estimated"], nullable=True
                ),
                "confidence": {"type": ["number", "null"]},
            },
        },
        "bounding_box": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "north": {"type": "number"},
                "south": {"type": "number"},
                "east": {"type": "number"},
                "west": {"type": "number"},
            },
        },
        "map_urls": {
            "type": ["object", "null"],
            "properties": {
                "google_maps": make_nullable("string"),
                "openstreetmap": make_nullable("string"),
            },
        },
        "related_places": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "PlaceID": ulid_field(),
                    "relationship": make_nullable("string"),
                },
            },
        },
        "hierarchy": {"type": ["array", "object", "null"]},
        # --- schema.org-aligned additive fields (adopted 2026-10-27; see
        # docs/current/design/SCHEMA_ORG_REVIEW.md). All nullable + closed; our own strict idiom,
        # not schema.org's loose shapes. ---
        # sameAs: authoritative external identity URLs (Wikidata/GeoNames/Wikipedia/OSM). The
        # interoperability cross-walk + a stronger dedup key than name strings. null-over-guess.
        "sameAs": {"type": ["array", "null"], "items": url_field()},
        # ID-typed hierarchy edges (schema.org containedInPlace/containsPlace). Distinct from the
        # free-form `hierarchy`/`related_places`: these point at real PlaceIDs so the containment
        # graph is traversable. Modern-identity cross-walk — the as-of-date place name stays in
        # historical_names + the event-mention date context.
        "containedInPlace": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "PlaceID": ulid_field(nullable=True),
                    "name": make_nullable("string"),
                },
            },
        },
        "containsPlace": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "PlaceID": ulid_field(nullable=True),
                    "name": make_nullable("string"),
                },
            },
        },
        # geo: our CLOSED GeoShape (schema.org geo -> GeoShape). Adds line/polygon/circle beyond the
        # point `coordinates` + box `bounding_box` — for a beach (line), a sector/front (polygon), a
        # river (polyline). Vertices as our own {lat,long} objects (NOT schema.org's lat,long-string).
        # Populated ONLY when a source/map legibly provides the shape (null-over-fake) + provenance.
        "geo": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "shape_type": enum_field(
                    ["point", "line", "polygon", "circle"], nullable=True
                ),
                "vertices": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "lat": {"type": "number", "minimum": -90, "maximum": 90},
                            "long": {"type": "number", "minimum": -180, "maximum": 180},
                        },
                    },
                },
                # circle convenience (schema.org GeoShape.circle): center + radius in metres.
                "radius_m": {"type": ["number", "null"], "minimum": 0},
                # Provenance for the shape (a hand-traced map polygon is approximate, not survey-grade).
                "source_date": make_nullable("string"),
                "SourceSectionID": ulid_field(nullable=True),
                "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            },
        },
        # additionalProperty: typed escape hatch (schema.org additionalProperty/PropertyValue) for
        # long-tail military-geographic attributes with no dedicated field (grid_reference,
        # hill_number, map designation). Our CLOSED {propertyID,value,unitCode} shape. Named fields
        # (e.g. elevation_m below) are preferred; this is for the ENUMERABLE-only-at-runtime rest.
        "additionalProperty": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "propertyID": {"type": "string"},
                    "value": {"type": ["string", "number", "boolean", "null"]},
                    "unitCode": make_nullable("string"),
                },
            },
        },
        # elevation_m: DEDICATED numeric (metres) — a first-class, unit-fixed, queryable terrain
        # attribute (Hill 400, the Hürtgen heights). schema.org names `elevation` on GeoCoordinates;
        # we give it a named field rather than burying it in additionalProperty. null-over-guess.
        "elevation_m": {"type": ["number", "null"]},
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Event_Name": make_nullable("string"),
                    "Sub_eventID": ulid_field(nullable=True),
                    "Sub_event_Name": make_nullable("string"),
                    "book": make_nullable("string"),
                    "author": make_nullable("string"),
                    "series": make_nullable("string"),
                    "context": make_nullable("string"),
                    "original_text": make_nullable("string"),
                    "role_in_event": make_nullable("string"),
                    "date_context": make_nullable("string"),
                    "DateMentionID": ulid_field(nullable=True),
                    "nationality": make_nullable("string"),
                },
            },
        },
        "enrichment_status": enum_field(
            [
                "enriched",
                "not_found",
                "geocoded",
                "geocode_low_confidence",
                "geocode_not_found",
                # Transient geocoder outage (API timeout/5xx/exception): a NON-terminal, retriable
                # status set by places_grok_geocode._apply_result. Must be schema-legal or the
                # write-guard blocks it, silently defeating the transient-retry guarantee.
                "geocode_error",
            ],
            nullable=True,
        ),
        "last_enrichment_search": {"type": ["string", "null"]},
    },
}
