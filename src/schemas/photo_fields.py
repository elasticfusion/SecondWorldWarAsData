"""The fields of a web photo on an entity record's ``images[]`` (written by
src/enrichment/web_photos: an OpenSERP image search, every photo confirmed by Grok vision; owner
2026-11-23). One definition, shared by every entity that holds photos - equipment, people,
places, people_groups - so the four never drift apart. All nullable (older entries lack them).
"""

from src.schemas import enum_field, make_nullable

WEB_PHOTO_PROPERTIES = {
    "url": make_nullable("string"),  # the image file's URL
    "local_path": make_nullable(
        "string"
    ),  # its stored copy (output/photos/files/<sha256>.<ext>)
    "source": make_nullable("string"),
    "caption": make_nullable("string"),  # read from the page it is on
    # whether Grok vision confirmed the image shows the subject, and the check's state
    "vision_verified": {"type": ["boolean", "null"]},
    "vision_check": enum_field(["pending", "verified", "rejected"], nullable=True),
    "vision_reason": make_nullable("string"),
    "vision_checked_at": make_nullable("string"),
    "phash": make_nullable(
        "string"
    ),  # perceptual hash (duplicate rejection across sites)
    "candidate_source": make_nullable("string"),  # openserp | wikipedia | legacy
    # provenance: the page the photo is on, when it was taken, the kept page, the photo's bytes
    "source_page_url": make_nullable("string"),
    "candidate_added_at": make_nullable("string"),
    "page_sha256": make_nullable("string"),
    "sha256": make_nullable("string"),
    # Grok vision: a 1939-1945 photograph? and the copyright basis (owner: assumed public domain)
    "period_photo": enum_field(["yes", "no", "unsure"], nullable=True),
    "copyright_status": make_nullable("string"),
}
