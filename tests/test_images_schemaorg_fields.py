"""Images schema.org ImageObject additive fields (§5) — all nullable/additive; backward-compatible."""

import jsonschema
import pytest
import ulid

from src.schemas.images_output import IMAGES_OUTPUT_SCHEMA as S

ULID = str(ulid.new())


def _valid(rec):
    jsonschema.validate(rec, S)


def _invalid(rec):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rec, S)


def test_backward_compatible_old_record():
    _valid({"ImageID": ULID, "url": "https://x/a.jpg"})


def test_full_record_all_new_fields():
    _valid(
        {
            "ImageID": ULID,
            "url": "https://ibiblio.org/x/icn/37.jpg",
            "contentUrl": "https://ibiblio.org/x/icn/37.jpg",
            "thumbnailUrl": "https://ibiblio.org/x/icn/37_thumb.jpg",
            "creditText": "U.S. Army Signal Corps",
            "copyrightNotice": "Public domain (US Government work)",
            "copyrightHolder": "U.S. Army",
            "acquireLicensePage": "https://ibiblio.org/x/license.html",
            "contentLocation": {"PlaceID": ULID, "name": "Omaha Beach"},
            "dateCreated": "1944-06-06",
            "datePublished": "1951",
            "caption": "ALLIED INVASION CHIEFS. Left to right: General Bradley...",
            "width": 800,
            "height": 600,
            "encodingFormat": "image/jpeg",
            "exifData": {"Make": "Signal Corps"},
        }
    )


def test_new_fields_nullable():
    _valid(
        {
            "ImageID": ULID,
            "contentUrl": None,
            "thumbnailUrl": None,
            "creditText": None,
            "copyrightNotice": None,
            "copyrightHolder": None,
            "acquireLicensePage": None,
            "contentLocation": None,
            "dateCreated": None,
            "datePublished": None,
            "caption": None,
            "width": None,
            "height": None,
            "encodingFormat": None,
            "exifData": None,
        }
    )


def test_contentLocation_closed_shape():
    _valid(
        {"ImageID": ULID, "contentLocation": {"PlaceID": ULID, "name": "Utah Beach"}}
    )
    _invalid({"ImageID": ULID, "contentLocation": {"PlaceID": ULID, "bogus": 1}})


def test_dateCreated_distinct_from_ingestion_dates():
    # dateCreated (taken) coexists with the ingestion dates — they are different fields
    _valid(
        {
            "ImageID": ULID,
            "dateCreated": "1944-06-06",
            "url_capture_date": "2026-10-27",
            "extracted_date": "2026-10-27",
        }
    )


def test_width_height_integer():
    _valid({"ImageID": ULID, "width": 1024, "height": 768})
    _invalid({"ImageID": ULID, "width": "big"})
    _invalid({"ImageID": ULID, "height": -5})  # minimum 0


def test_license_vocab_coexists():
    # the richer licensing fields coexist with the existing single `license` string
    _valid(
        {
            "ImageID": ULID,
            "license": "PD",
            "creditText": "Signal Corps",
            "copyrightHolder": "U.S. Army",
        }
    )


def test_top_level_still_closed():
    _invalid({"ImageID": ULID, "totally_new_unknown_field": 1})
