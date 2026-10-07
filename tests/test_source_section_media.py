"""Tests for source_section media fetch (Wikipedia photos/maps -> first-class images)."""

import json
from unittest.mock import patch

import jsonschema

from src.extraction import source_section_media as ssm
from src.schemas.images_output import IMAGES_OUTPUT_SCHEMA


def test_classify_photo_map_and_skip():
    assert ssm._classify("German_Wacht_Am_Rhein_Offensive_Plan.png") == "map"
    assert ssm._classify("6th_Armored_Division_in_Belgium_1945.jpg") == "photo"
    assert ssm._classify("Semi-protection-shackle.svg") is None
    assert ssm._classify("Commons-logo.svg") is None
    assert ssm._classify("notanimage.pdf") is None


def test_no_operation_is_noop(tmp_path):
    assert ssm.fetch_section_media({"operation": None}, tmp_path) == 0


def test_build_record_validates_and_cross_links():
    rec = ssm._build_image_record(
        source_section_id="01HX7YZABCDEFGHJKMNPQRSTVW",
        event_id="01HX7YZABCDEFGHJKMNPQRSTVX",
        file_title="File:German_Wacht_Am_Rhein_Offensive_Plan.png",
        kind="map",
        resolved={
            "url": "https://upload.wikimedia.org/x.png",
            "license": "Public domain",
            "attribution": "US Army",
            "copyright_status": None,
        },
        local_copy=None,
    )
    jsonschema.validate(rec, IMAGES_OUTPUT_SCHEMA)
    assert rec["image_type"] == "map"
    assert rec["SourceSectionID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"
    assert rec["EventID"] == "01HX7YZABCDEFGHJKMNPQRSTVX"
    assert rec["license"] == "Public domain"


def test_null_license_preserved():
    # null-over-fake: a missing Commons license stays None, not a guess.
    rec = ssm._build_image_record(
        source_section_id="01HX7YZABCDEFGHJKMNPQRSTVW",
        event_id=None,
        file_title="File:x.jpg",
        kind="photo",
        resolved={"url": "http://x", "license": None, "attribution": None},
        local_copy=None,
    )
    assert rec["license"] is None


def test_fetch_writes_image_records(tmp_path):
    rec = {
        "SourceSectionID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "EventID": "01HX7YZABCDEFGHJKMNPQRSTVX",
        "operation": {
            "name": "Battle of the Bulge",
            "wikipedia_title": "Battle of the Bulge",
        },
    }
    with (
        patch.object(
            ssm,
            "_list_article_image_titles",
            return_value=["File:Map_offensive.png", "File:Commons-logo.svg"],
        ),
        patch.object(
            ssm,
            "_resolve_commons_image",
            return_value={
                "url": "http://x.png",
                "license": "Public domain",
                "attribution": None,
            },
        ),
    ):
        n = ssm.fetch_section_media(rec, tmp_path, download=False)
    # the logo is skipped by the classifier, so only the map is written
    assert n == 1
    files = list((tmp_path / "images").glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    jsonschema.validate(saved, IMAGES_OUTPUT_SCHEMA)
    assert saved["image_type"] == "map"
    assert saved["SourceSectionID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"
