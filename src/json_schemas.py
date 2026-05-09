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
        "MapID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "map_title": {"type": "string", "minLength": 1},
        "source_book": {"type": "string", "minLength": 1},
        "source_author": {"type": "string", "minLength": 1},
        "source_series": {"type": ["string", "null"]},
        "page_number": {"type": ["integer", "null"]},
        "figure_number": {"type": ["string", "null"]},
        "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Event_Name": {"type": ["string", "null"]},
        "Sub_eventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
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
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
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
WEATHER_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": [
        "Event_Name",
        "EventID",
        "Sub-event_Name",
        "Sub-eventID",
        "Weather_Mentions",
    ],
    "properties": {
        "Event_Name": {"type": "string"},
        "EventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Sub-event_Name": {"type": "string"},
        "Sub-eventID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "Weather_Mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "WeatherMentionID",
                    "place_name",
                    "date",
                    "weather_description",
                    "original_text",
                ],
                "properties": {
                    "WeatherMentionID": {
                        "type": "string",
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "place_name": {"type": "string"},
                    "PlaceMentionID": {"type": ["string", "null"]},
                    "date": {"type": "string"},
                    "DateMentionID": {"type": ["string", "null"]},
                    "weather_description": {"type": "string"},
                    "temperature": {"type": ["number", "null"]},
                    "temperature_unit": {"type": ["string", "null"]},
                    "measurement_system": {"type": ["string", "null"]},
                    "notable_impact": {"type": ["string", "null"]},
                    "original_text": {"type": "string"},
                },
            },
        },
    },
}

# Logistics schema (validated output format)
LOGISTICS_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": [
        "LogisticsID",
        "logistics_type",
        "category",
        "description",
        "severity",
        "temporal",
        "status",
        "event_mentions",
        "extracted_date",
    ],
    "properties": {
        "LogisticsID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "logistics_type": {
            "type": "string",
            "enum": [
                "supply_shortage",
                "supply_excess",
                "delivery_delay",
                "transport_disruption",
            ],
        },
        "category": {
            "type": "string",
            "enum": [
                "ammunition",
                "fuel",
                "food",
                "medical",
                "equipment",
                "personnel",
                "general",
            ],
        },
        "description": {"type": "string"},
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low"],
        },
        "temporal": {
            "type": "object",
            "required": ["date_start", "date_type"],
            "properties": {
                "date_start": {"type": "string"},
                "date_type": {"type": "string", "enum": ["specific", "range"]},
                "date_end": {"type": ["string", "null"]},
                "DateMentionID": {"type": ["string", "null"]},
            },
        },
        "status": {
            "type": "string",
            "enum": ["unresolved", "in_progress", "resolved", "worsened"],
        },
        "event_mentions": {"type": "array", "minItems": 1},
        "extracted_date": {"type": "string"},
        "delivery_method": {
            "type": ["string", "null"],
            "enum": [
                "sea_transport",
                "air_delivery",
                "ground_transport",
                "rail",
                "pipeline",
                "mixed",
                None,
            ],
        },
        "quantity": {"type": ["object", "null"]},
        "resolution": {"type": ["object", "null"]},
        "impacted_organizations": {"type": ["array", "null"]},
        "impacted_people": {"type": ["array", "null"]},
        "impacted_places": {"type": ["array", "null"]},
        "impacted_equipment": {"type": ["array", "null"]},
        "weather_impact": {"type": ["object", "null"]},
    },
}

# Images schema (output file format)
IMAGES_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": ["ImageID", "image_title", "content_type", "extracted_date"],
    "properties": {
        "ImageID": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
        "image_title": {"type": "string"},
        "image_type": {"type": "string"},
        "content_type": {"type": "string"},
        "source": {"type": ["string", "null"]},
        "EventID": {"type": ["string", "null"]},
        "Sub-eventID": {"type": ["string", "null"]},
        "PlaceMentionID": {"type": ["string", "null"]},
        "DateMentionID": {"type": ["string", "null"]},
        "url": {"type": ["string", "null"]},
        "local_copy": {"type": ["string", "null"]},
        "license": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "extracted_date": {"type": "string"},
    },
}

# Bibliography schema (output file format)
BIBLIOGRAPHY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "type": "object",
    "required": ["BibliographyID", "title", "citation", "mentions"],
    "properties": {
        "BibliographyID": {
            "type": "string",
            "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
        },
        "title": {"type": "string"},
        "alt_title": {"type": ["string", "null"]},
        "citation": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "author": {"type": "array", "items": {"type": "string"}},
                "publisher": {"type": ["string", "null"]},
                "publication_date": {"type": ["string", "null"]},
            },
        },
        "availability": {"type": "string"},
        "resource_urls": {"type": "array", "items": {"type": "string"}},
        "license": {"type": ["string", "null"]},
        "mentions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["MentionID", "EventID", "Sub-eventID"],
                "properties": {
                    "MentionID": {
                        "type": "string",
                        "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
                    },
                    "EventID": {"type": "string"},
                    "Sub-eventID": {"type": "string"},
                    "reference_type": {"type": "string"},
                    "reference_number": {"type": ["string", "integer", "null"]},
                    "verbatim_reference": {"type": "string"},
                },
            },
        },
    },
}


# --- Item-level schemas for validating individual Grok response items ---

CASUALTY_ITEM_SCHEMA = {
    "type": "object",
    "required": ["type", "description"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["wounded", "killed", "casualties", "pow", "missing"],
        },
        "description": {"type": "string"},
        "side": {
            "type": "string",
            "enum": ["allied", "axis", "civilian", "unknown"],
        },
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
