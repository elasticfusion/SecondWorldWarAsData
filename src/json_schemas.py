"""JSON schemas for validation."""

from src.schemas import SCHEMA_VERSION, ULID_PATTERN

# Event schema
EVENT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["Chapter", "Event"],
    "properties": {
        "Chapter": {"type": "string"},
        "Event": {
            "type": "object",
            "required": ["EventID", "Sub-events"],
            "properties": {
                "EventID": {"type": "string", "pattern": ULID_PATTERN},
                "Sub-events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "Sub-eventID",
                            "Sub-event_summary",
                            "Sub-event_fulltext",
                        ],
                        "properties": {
                            "Sub-eventID": {
                                "type": "string",
                                "pattern": ULID_PATTERN,
                            },
                            "Sub-event_summary": {"type": "string"},
                            "Sub-event_fulltext": {"type": "object"},
                            "Endnote_References": {"type": "array"},
                            "Footnote_References": {"type": "array"},
                        },
                    },
                },
            },
        },
    },
}

# Date schema
DATE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Date_Mentions",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": ULID_PATTERN},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": ULID_PATTERN},
        "Date_Mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "DateMentionID",
                    "date_start",
                    "original_text",
                ],
                "properties": {
                    "DateMentionID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "date_start": {"type": "string"},
                    "date_end": {"type": ["string", "null"]},
                    "time_start": {"type": ["string", "null"]},
                    "time_end": {"type": ["string", "null"]},
                    "time_precision": {
                        "type": ["string", "null"],
                        "enum": ["exact", "approximate", None],
                    },
                    "date_precision": {
                        "type": ["string", "null"],
                        "enum": [
                            "exact",
                            "early",
                            "mid",
                            "late",
                            "spring",
                            "summer",
                            "fall",
                            "winter",
                            None,
                        ],
                    },
                    "time_source": {
                        "type": ["string", "null"],
                        "enum": [
                            "German",
                            "Allied",
                            "Zulu",
                            "GMT",
                            "CET",
                            "Local",
                            None,
                        ],
                    },
                    "original_text": {"type": "string"},
                },
            },
        },
    },
}

# Place schema
PLACE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Place_Mentions",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": ULID_PATTERN},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": ULID_PATTERN},
        "Place_Mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "PlaceMentionID",
                    "current_name",
                    "source_language",
                    "geography_type",
                    "original_text",
                ],
                "properties": {
                    "PlaceMentionID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "current_name": {"type": "string"},
                    "historical_name": {"type": ["string", "null"]},
                    "source_language": {"type": "string"},
                    "latitude": {"type": ["number", "null"]},
                    "longitude": {"type": ["number", "null"]},
                    "bounding_box": {
                        "type": ["object", "null"],
                        "properties": {
                            "north": {"type": "number"},
                            "south": {"type": "number"},
                            "east": {"type": "number"},
                            "west": {"type": "number"},
                        },
                    },
                    "geography_type": {
                        "type": "string",
                        "enum": [
                            "country",
                            "state",
                            "province",
                            "city",
                            "town",
                            "village",
                            "military_theater",
                            "military_base",
                            "fortification",
                            "region",
                            "peninsula",
                            "island",
                            "archipelago",
                            "mountain",
                            "mountain_range",
                            "valley",
                            "plain",
                            "sea",
                            "ocean",
                            "river",
                            "lake",
                            "channel",
                        ],
                    },
                    "precision": {
                        "type": ["string", "null"],
                        "enum": ["exact", "approximate", "region_center", None],
                    },
                    "confidence": {"type": ["number", "null"]},
                    "date_context": {"type": ["string", "null"]},
                    "original_text": {"type": "string"},
                    "route": {
                        "type": ["array", "null"],
                        "items": {
                            "type": "object",
                            "required": ["sequence", "current_name", "geography_type"],
                            "properties": {
                                "sequence": {"type": "integer"},
                                "current_name": {"type": "string"},
                                "historical_name": {"type": ["string", "null"]},
                                "latitude": {"type": ["number", "null"]},
                                "longitude": {"type": ["number", "null"]},
                                "bounding_box": {"type": ["object", "null"]},
                                "geography_type": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


# Supplemental Material schema
SUPPLEMENTAL_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Supplemental_Material",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": ULID_PATTERN},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": ULID_PATTERN},
        "Supplemental_Material": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "MaterialID",
                    "EventID",
                    "Sub-eventID",
                    "reference_type",
                    "verbatim_reference",
                    "availability",
                ],
                "properties": {
                    "MaterialID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "EventID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "Sub-eventID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "reference_type": {
                        "type": "string",
                        "enum": ["endnote", "footnote", "bibliography"],
                    },
                    "reference_number": {"type": ["string", "integer", "null"]},
                    "verbatim_reference": {"type": "string"},
                    "prose_content": {"type": ["string", "null"]},
                    "material_category": {
                        "type": "string",
                        "enum": ["referenced_material", "supplemental_information"],
                    },
                    "content_class": {
                        "type": "string",
                        "enum": [
                            # 3-class model (owner 2026-10-16): citation = strictly a
                            # document reference (-> bibliography/Phase-3); prose =
                            # strictly narrative (-> Phase-2 notes-event); hybrid =
                            # BOTH (prose_content + citation). Legacy names kept for
                            # back-compat (document_reference≈citation,
                            # factual_content≈prose, ambiguous→human review).
                            "citation",
                            "prose",
                            "hybrid",
                            "document_reference",
                            "factual_content",
                            "ambiguous",
                        ],
                    },
                    "citation": {
                        "type": ["object", "null"],
                        "required": ["title"],
                        "properties": {
                            "author": {"type": "array", "items": {"type": "string"}},
                            "title": {"type": "string"},
                            "alt_title": {"type": ["string", "null"]},
                            "publisher": {"type": ["string", "null"]},
                            "publication_date": {"type": ["string", "null"]},
                            "first_edition_date": {"type": ["string", "null"]},
                            "publication_location": {"type": ["string", "null"]},
                            "publication_country": {"type": ["string", "null"]},
                            "isbn": {"type": ["string", "null"]},
                            "isbn_edition": {"type": ["string", "null"]},
                            "pages": {"type": ["string", "null"]},
                            "volume": {"type": ["string", "null"]},
                            "edition": {"type": ["string", "null"]},
                            "translator": {"type": ["string", "null"]},
                            "periodical_name": {"type": ["string", "null"]},
                            "document_type": {"type": ["string", "null"]},
                            "author_death_date": {"type": ["string", "null"]},
                        },
                    },
                    "availability": {
                        "type": "string",
                        "enum": ["online", "offline", "archive", "unknown"],
                    },
                    "resource_urls": {"type": "array", "items": {"type": "string"}},
                    "archive_reference_number": {"type": ["string", "null"]},
                    "archive_physical_address": {"type": ["string", "null"]},
                    "url_validation_status": {"type": ["string", "null"]},
                    "url_validation_date": {"type": ["string", "null"]},
                    "license": {"type": ["string", "null"]},
                    "license_notes": {"type": ["string", "null"]},
                },
            },
        },
    },
}

# People schema
PEOPLE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["people"],
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["PersonID", "name"],
                "properties": {
                    "PersonID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "name": {"type": "string"},
                    "birth_date": {"type": ["string", "null"]},
                    "death_date": {"type": ["string", "null"]},
                },
            },
        },
    },
}

# People Groups schema
PEOPLE_GROUPS_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["groups"],
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["GroupID", "name"],
                "properties": {
                    "GroupID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "name": {"type": "string"},
                },
            },
        },
    },
}

# Equipment schema
EQUIPMENT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["equipment"],
    "properties": {
        "equipment": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["EquipmentID", "name"],
                "properties": {
                    "EquipmentID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "name": {"type": "string"},
                },
            },
        },
    },
}

# Map schema
MAP_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": [
        "MapID",
        "map_title",
        "source_book",
        "source_author",
        "EventID",
        "Sub_eventID",
        "local_path",
        "extracted_date",
    ],
    "properties": {
        "MapID": {"type": "string", "pattern": ULID_PATTERN},
        "map_title": {"type": "string", "minLength": 1},
        "source_book": {"type": "string", "minLength": 1},
        "source_author": {"type": "string", "minLength": 1},
        "source_series": {"type": ["string", "null"]},
        "page_number": {"type": ["integer", "null"]},
        "figure_number": {"type": ["string", "null"]},
        "EventID": {"type": "string", "pattern": ULID_PATTERN},
        "Event_Name": {"type": ["string", "null"]},
        "Sub_eventID": {"type": "string", "pattern": ULID_PATTERN},
        "Sub_event_Name": {"type": ["string", "null"]},
        "place_name": {"type": ["string", "null"]},
        "PlaceMentionID": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "DateMentionID": {"type": ["string", "null"]},
        "local_path": {"type": "string"},
        "local_image_path": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "file_format": {
            "type": ["string", "null"],
            "enum": ["jpg", "png", "tif", "pdf", None],
        },
        "map_type": {
            "type": ["string", "null"],
            "enum": ["tactical", "strategic", "political", "terrain", "other", None],
        },
        "description": {"type": ["string", "null"]},
        "extracted_date": {"type": "string"},
    },
}

# Casualties schema
CASUALTIES_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["casualties"],
    "properties": {
        "casualties": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "CasualtyID",
                    "type",
                    "description",
                    "event_context",
                    "source",
                ],
                "properties": {
                    "CasualtyID": {
                        "type": "string",
                        "pattern": ULID_PATTERN,
                    },
                    "type": {
                        "type": "string",
                        "enum": ["wounded", "killed", "casualties", "pow", "missing"],
                    },
                    "description": {"type": "string"},
                    "side": {
                        "type": ["string", "null"],
                        "enum": [
                            "allied",
                            "axis",
                            "civilian",
                            "unknown",
                            None,
                        ],
                    },
                    "count": {
                        "type": ["object", "null"],
                    },
                    "date": {
                        "type": ["object", "null"],
                        "properties": {
                            "DateID": {"type": ["string", "null"]},
                            "date_string": {"type": ["string", "null"]},
                            "precision": {"type": ["string", "null"]},
                        },
                    },
                    "event_context": {
                        "type": "object",
                        "required": ["EventID"],
                        "properties": {
                            "EventID": {"type": "string"},
                            "Sub-eventID": {"type": ["string", "null"]},
                        },
                    },
                    "source": {
                        "type": "object",
                        "required": ["book", "chapter"],
                        "properties": {
                            "book": {"type": "string"},
                            "chapter": {"type": "string"},
                            "paragraph_number": {"type": ["integer", "null"]},
                        },
                    },
                    "impacted_organizations": {"type": ["array", "null"]},
                    "impacted_people": {"type": ["array", "null"]},
                    "impacted_places": {"type": ["array", "null"]},
                },
            },
        },
    },
}


# Weather schema (extraction-time format)

# Logistics schema (validated output format)

# Images schema (output file format)

# Bibliography schema (output file format)


# --- Item-level schemas for validating individual Grok response items ---

CASUALTY_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["type", "description"],
    "properties": {
        "CasualtyID": {"type": "string"},
        # type MUST include non_battle (the prompt emits it for weather/disease/accident
        # casualties); a stale narrow enum here silently dropped every non_battle item.
        "type": {
            "type": "string",
            "enum": [
                "wounded",
                "killed",
                "casualties",
                "pow",
                "missing",
                "non_battle",
            ],
        },
        # cause classifies HOW (combat vs weather_exposure/disease/accident). The model emits
        # it per prompts/casualties.yaml; omitting it from properties dropped the whole item.
        "cause": {
            "type": ["string", "null"],
            "enum": [
                "combat",
                "weather_exposure",
                "disease",
                "accident",
                "other",
                None,
            ],
        },
        "description": {"type": "string"},
        # original_text is the provenance anchor (mandatory in the prompt) — it MUST be an
        # accepted property or every sourced casualty item is rejected (the 0-on-684 bug).
        "original_text": {"type": ["string", "null"]},
        "side": {
            "type": "string",
            "enum": ["allied", "axis", "civilian", "neutral", "unknown"],
        },
        "nationality": {"type": ["string", "null"]},
        "count": {"type": ["object", "integer", "number", "null"]},
        # qualitative magnitude / comparative flag (schema 2.27): a casualty fact is valid
        # without an absolute count ("heavy casualties").
        "magnitude": {
            "type": ["string", "null"],
            "enum": ["none", "light", "moderate", "heavy", "severe", None],
        },
        "is_comparative": {"type": ["boolean", "null"]},
        "date_string": {"type": ["string", "null"]},
        "date": {"type": ["object", "string", "null"]},
        "PersonID": {"type": ["string", "null"]},
        "PlaceID": {"type": ["string", "null"]},
        "DateID": {"type": ["string", "null"]},
        "source": {"type": ["object", "string", "null"]},
        "event_context": {"type": ["object", "null"]},
        "impacted_organizations": {"type": "array"},
        "impacted_people": {"type": "array"},
        "impacted_places": {"type": "array"},
        "impacted_equipment": {"type": "array"},
        "impacted_logistics": {"type": "array"},
    },
}

PEOPLE_GROUP_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "patternProperties": {"^_": {}},
    "required": ["group_name"],
    "properties": {
        "GroupID": {"type": "string"},
        "group_name": {"type": "string"},
        "group_type": {"type": ["string", "null"]},
        "nationality": {"type": ["string", "null"]},
        "country_of_origin": {"type": ["string", "null"]},
        "source_language": {"type": ["string", "null"]},
        "military_hierarchy": {"type": ["string", "null"]},
        "alliance_membership": {"type": "array"},
        "description": {"type": ["string", "null"]},
        "parent_organization": {"type": ["string", "null"]},
        "member_countries": {"type": "array"},
        "sub_organizations": {"type": "array"},
        "common_name": {"type": ["string", "null"]},
        "members": {"type": "array"},
        "event_mentions": {"type": "array"},
    },
}
