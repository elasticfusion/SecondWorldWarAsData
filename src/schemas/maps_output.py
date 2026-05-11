"""Strict output schema for maps files (output/maps/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    make_nullable,
    ulid_field,
)

MAPS_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Maps Output File",
    "type": "object",
    "required": ["MapID"],
    "additionalProperties": True,  # Maps have source file path keys for tracking
    "properties": {
        **METADATA_PROPERTIES,
        "MapID": ulid_field(),
        "map_title": make_nullable("string"),
        "map_type": make_nullable("string"),
        "description": make_nullable("string"),
        "source_book": make_nullable("string"),
        "source_author": make_nullable("string"),
        "source_series": make_nullable("string"),
        "source_url": make_nullable("string"),
        "local_path": make_nullable("string"),
        "local_image_path": make_nullable("string"),
        "file_format": make_nullable("string"),
        "storage_backend": make_nullable("string"),
        "page_number": {"type": ["string", "integer", "null"]},
        "figure_number": {"type": ["string", "integer", "null"]},
        "extracted_date": make_nullable("string"),
        "EventID": ulid_field(nullable=True),
        "Event_Name": make_nullable("string"),
        "Sub_eventID": ulid_field(nullable=True),
        "Sub_event_Name": make_nullable("string"),
        "place_name": make_nullable("string"),
        "PlaceMentionID": ulid_field(nullable=True),
        "date": make_nullable("string"),
        "DateMentionID": ulid_field(nullable=True),
    },
}
