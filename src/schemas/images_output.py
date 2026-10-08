"""Strict output schema for image files (output/images/*.json).

Images are media records (photos/diagrams) discovered in sources, linked to the entity
graph via Event/Place/Date mention IDs. Previously there was NO enforced output schema (only
the extraction-time json_schemas.IMAGES_SCHEMA, which real records did not satisfy) — this
closes that gap so image records are version-gated like every other entity.
"""

from src.schemas import (
    METADATA_PROPERTIES,
    entity_version,
    make_nullable,
    ulid_field,
)

IMAGES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("images"),
    "title": "Images Output File",
    "type": "object",
    "required": ["ImageID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "ImageID": ulid_field(),
        "image_title": make_nullable("string"),
        "image_type": make_nullable("string"),
        "content_type": make_nullable("string"),
        "resource_type": make_nullable("string"),
        "source": make_nullable("string"),
        "url": make_nullable("string"),
        "local_copy": make_nullable("string"),
        "url_capture_date": make_nullable("string"),
        "license": make_nullable("string"),
        "description": make_nullable("string"),
        "extracted_date": make_nullable("string"),
        # Entity-graph links (nullable — an image may not resolve to all of them)
        "EventID": ulid_field(nullable=True),
        "Event_Name": make_nullable("string"),
        "SourceSectionID": ulid_field(nullable=True),
        "Sub-eventID": ulid_field(nullable=True),
        "Sub-event_Name": make_nullable("string"),
        "place_name": make_nullable("string"),
        "PlaceMentionID": ulid_field(nullable=True),
        "date": make_nullable("string"),
        "DateMentionID": ulid_field(nullable=True),
        # Grok-vision captioning pass (optional; present when images.vision_caption is on).
        "caption_confidence": {"type": ["number", "null"]},
        "caption_method": make_nullable("string"),
        "needs_review": {"type": ["boolean", "null"]},
    },
}
