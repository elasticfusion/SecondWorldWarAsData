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
    url_field,
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
        # --- schema.org ImageObject/MediaObject-aligned additive fields (adopted 2026-10-27; see
        # docs/current/design/SCHEMA_ORG_REVIEW.md §5). All nullable; our strict idiom. Existing
        # url/local_copy/license/description/SourceSectionID(=isBasedOn)/PlaceMentionID/url_capture_date
        # /extracted_date/caption_confidence/needs_review are KEPT (no schema.org equivalent for the
        # last two). ---
        # contentUrl / thumbnailUrl (schema.org): the canonical content URL vs a thumbnail, distinct
        # from our ingestion `url`/`local_copy`.
        "contentUrl": url_field(nullable=True),
        "thumbnailUrl": url_field(nullable=True),
        # Licensing (schema.org ImageObject + Google Images): richer than the single `license` string.
        "creditText": make_nullable("string"),
        "copyrightNotice": make_nullable("string"),
        "copyrightHolder": make_nullable("string"),
        "acquireLicensePage": url_field(nullable=True),
        # contentLocation (schema.org): the PLACE the image DEPICTS, as a typed PlaceID link (M3/M5
        # compute this). Distinct from PlaceMentionID (a mention) — this is the resolved depicted place.
        "contentLocation": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "PlaceID": ulid_field(nullable=True),
                "name": make_nullable("string"),
            },
        },
        # dateCreated / datePublished (schema.org): when the photo was TAKEN / first PUBLISHED —
        # historically meaningful + DISTINCT from our ingestion dates (url_capture_date/extracted_date).
        # null-over-guess (rarely known precisely).
        "dateCreated": make_nullable("string"),
        "datePublished": make_nullable("string"),
        # caption (schema.org): the display caption text (the figure caption as shown). Our vision
        # caption lives via description + caption_confidence; this is the source-shown caption.
        "caption": make_nullable("string"),
        # Media technical metadata (schema.org ImageObject): width/height in px, MIME, EXIF bag.
        "width": {"type": ["integer", "null"], "minimum": 0},
        "height": {"type": ["integer", "null"], "minimum": 0},
        "encodingFormat": make_nullable("string"),
        "exifData": {"type": ["object", "null"]},
    },
}
