"""JSON schemas for validation."""

SCHEMA_VERSION = "1.0.0"

# Event schema
EVENT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": ["Chapter", "Event"],
    "properties": {
        "Chapter": {"type": "string"},
        "Event": {
            "type": "object",
            "required": ["EventID", "Sub-events"],
            "properties": {
                "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
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
                                "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
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
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Date_Mentions",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "date_start": {"type": "string"},
                    "date_end": {"type": ["string", "null"]},
                    "time_start": {"type": ["string", "null"]},
                    "time_end": {"type": ["string", "null"]},
                    "time_precision": {"type": ["string", "null"]},
                    "time_source": {"type": ["string", "null"]},
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
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Place_Mentions",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "current_name": {"type": "string"},
                    "historical_name": {"type": ["string", "null"]},
                    "source_language": {"type": "string"},
                    "latitude": {"type": ["number", "null"]},
                    "longitude": {"type": ["number", "null"]},
                    "bounding_box_100km": {
                        "type": ["object", "null"],
                        "properties": {
                            "north": {"type": "number"},
                            "south": {"type": "number"},
                            "east": {"type": "number"},
                            "west": {"type": "number"},
                        },
                    },
                    "geography_type": {"type": "string"},
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
                                "bounding_box_100km": {"type": ["object", "null"]},
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
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Supplemental_Material",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Supplemental_Material": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "MaterialID",
                    "EventID",
                    "Sub-eventID",
                    "reference_type",
                    "reference_number",
                    "verbatim_reference",
                    "citation",
                    "availability",
                ],
                "properties": {
                    "MaterialID": {
                        "type": "string",
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "EventID": {
                        "type": "string",
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "Sub-eventID": {
                        "type": "string",
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "reference_type": {
                        "type": "string",
                        "enum": ["endnote", "footnote", "bibliography"],
                    },
                    "reference_number": {"type": ["string", "integer", "null"]},
                    "verbatim_reference": {"type": "string"},
                    "material_category": {
                        "type": "string",
                        "enum": ["referenced_material", "supplemental_information"],
                    },
                    "content_class": {
                        "type": "string",
                        "enum": [
                            "document_reference",
                            "factual_content",
                            "ambiguous",
                        ],
                    },
                    "citation": {
                        "type": "object",
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
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
    "required": ["MapID"],
    "properties": {
        "MapID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
    },
}

# Casualties schema
CASUALTIES_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": ["casualties"],
    "properties": {
        "casualties": {"type": "array"},
    },
}


# --- Item-level schemas for validating individual Grok response items ---

CASUALTY_ITEM_SCHEMA = {
    "type": "object",
    "required": ["type", "description"],
    "properties": {
        "type": {"type": "string", "enum": ["wounded", "killed", "casualties", "pow"]},
        "description": {"type": "string"},
        "count": {"type": "object"},
    },
}

PEOPLE_GROUP_ITEM_SCHEMA = {
    "type": "object",
    "required": ["group_name"],
    "properties": {
        "group_name": {"type": "string"},
        "group_type": {"type": ["string", "null"]},
        "nationality": {"type": ["string", "null"]},
        "event_mentions": {"type": "array"},
    },
}
