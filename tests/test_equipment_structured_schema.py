"""Expanded equipment schema: structured specifications/external_data/images/variants
(variants managed in-file, with their own specs+images); backward-compatible with loose
shapes; the images mapper produces the structured shape."""

import jsonschema
import pytest

from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA as S
from src.enrichment.equipment_images import _as_candidate

UL = "01ABCDEFGH0123456789ABCDEF"


def test_schema_valid_draft7():
    jsonschema.Draft7Validator.check_schema(S)


def test_structured_record_validates():
    rec = {
        "EquipmentID": UL,
        "common_name": "M4 Sherman",
        "specifications": {
            "weight_kg": 30300,
            "main_armament": "75mm M3 gun",
            "crew": 5,
        },
        "external_data": {
            "grokipedia_url": "https://grokipedia.com/M4",
            "wikipedia_url": "https://en.wikipedia.org/wiki/M4_Sherman",
            "additional_sources": [
                {
                    "source_type": "museum",
                    "source_name": "Tank Museum",
                    "url": "https://x",
                    "data_points": [
                        {"field": "production", "value": "49234", "verified": True}
                    ],
                }
            ],
        },
        "images": [
            {
                "url": "https://u/img.jpg",
                "local_path": "filestore/x.jpg",
                "source": "commons",
                "license": "CC BY-SA",
                "caption": "M4",
                "image_scope": "representative",
                "vision_verified": True,
            }
        ],
    }
    jsonschema.validate(rec, S)


def test_variant_with_own_specs_and_images_in_file():
    rec = {
        "EquipmentID": UL,
        "common_name": "M4 Sherman",
        "variants": [
            {
                "variant_name": "M4A3E8",
                "differences": "76mm, HVSS",
                "alternate_names": ["Easy Eight"],
                "specifications": {"main_armament": "76mm gun"},
                "images": [
                    {
                        "url": "https://u/e8.jpg",
                        "image_scope": "representative",
                        "vision_verified": False,
                    }
                ],
            }
        ],
    }
    jsonschema.validate(rec, S)  # variant carries its own specs + images, same file


def test_backward_compat_loose_shapes():
    jsonschema.validate(
        {
            "EquipmentID": UL,
            "variants": ["M4A1", "M4A3"],
            "specifications": {"anything": "goes"},
        },
        S,
    )


def test_bad_image_scope_rejected():
    rec = {"EquipmentID": UL, "images": [{"url": "x", "image_scope": "bogus"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rec, S)


def test_images_mapper_shape():
    # Phase 3 candidate shape: a type reference image, UNVERIFIED until Grok vision confirms it
    # (the Phase 2 mapper that stamped vision_verified without a check was removed).
    item = {
        "media_type": "photo",
        "url": "https://u/i.jpg",
        "source": "commons",
        "license": "CC",
        "title": "M4 tank",
    }
    img = _as_candidate(item, "wikipedia")
    assert img["url"] == "https://u/i.jpg"
    assert img["image_scope"] == "representative"  # default: type reference, not event
    assert img["vision_verified"] is False and img["vision_check"] == "pending"
    assert img["caption"] == "M4 tank" and img["candidate_source"] == "wikipedia"
    jsonschema.validate({"EquipmentID": UL, "images": [img]}, S)
