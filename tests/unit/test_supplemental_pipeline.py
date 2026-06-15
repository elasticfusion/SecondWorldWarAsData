"""Tests for supplemental_info_pipeline.py and validate_supplemental_urls.py."""

# pylint: disable=missing-function-docstring

import json
from unittest.mock import Mock, patch


from src.extraction.supplemental_info_pipeline import (
    _create_pseudo_event,
    _extract_entity_type,
)
from src.extraction.validate_supplemental_urls import (
    validate_material_urls,
    validate_url,
)


class TestCreatePseudoEvent:
    def test_creates_valid_structure(self):
        material = {
            "MaterialID": "01ABCDEFGHIJKLMNOPQRSTUVWX",
            "verbatim_reference": "The 35th Division suffered 500 casualties.",
            "reference_type": "endnote",
            "reference_number": "14",
        }
        result = _create_pseudo_event(material, "EV_001", "SE_001")

        assert result["Event"]["EventID"] == "EV_001"
        assert result["Event"]["Sub-events"][0]["Sub-eventID"] == "SE_001"
        assert (
            "35th Division"
            in result["Event"]["Sub-events"][0]["Sub-event_fulltext"]["1"]
        )

    def test_handles_empty_material(self):
        result = _create_pseudo_event({}, "EV_001", "SE_001")
        assert result["Event"]["EventID"] == "EV_001"
        assert result["Event"]["Sub-events"][0]["Sub-event_fulltext"]["1"] == ""


class TestExtractEntityType:
    def test_disabled_type_returns_none(self, tmp_path):
        config = {"dates": {"enabled": False}}
        result = _extract_entity_type("dates", {}, Mock(), tmp_path, config)
        assert result is None

    def test_unknown_type_returns_none(self, tmp_path):
        config = {}
        result = _extract_entity_type("unknown_type", {}, Mock(), tmp_path, config)
        assert result is None

    def test_exception_returns_none(self, tmp_path):
        config = {"dates": {"enabled": True}}
        with patch("src.extraction.dates.extract_dates", side_effect=Exception("fail")):
            result = _extract_entity_type(
                "dates", {"Event": {"Sub-events": []}}, Mock(), tmp_path, config
            )
        assert result is None


class TestValidateUrl:
    @patch("src.extraction.validate_supplemental_urls.requests.get")
    def test_valid_url(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        status, error = validate_url("http://example.com")
        assert status == "validated"
        assert error is None

    @patch("src.extraction.validate_supplemental_urls.requests.get")
    def test_broken_url(self, mock_get):
        mock_get.return_value = Mock(status_code=404)
        status, error = validate_url("http://example.com/missing")
        assert status == "broken"
        assert "404" in error

    @patch("src.extraction.validate_supplemental_urls.requests.get")
    def test_timeout(self, mock_get):
        import requests

        mock_get.side_effect = requests.Timeout()
        status, error = validate_url("http://slow.example.com")
        assert status == "timeout"

    @patch("src.extraction.validate_supplemental_urls.requests.get")
    def test_connection_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.ConnectionError("refused")
        status, error = validate_url("http://down.example.com")
        assert status == "invalid"


class TestValidateMaterialUrls:
    @patch("src.extraction.validate_supplemental_urls.validate_url")
    def test_all_valid(self, mock_validate):
        mock_validate.return_value = ("validated", None)
        material = {"resource_urls": ["http://a.com", "http://b.com"]}
        validate_material_urls(material)
        assert material["url_validation_status"] == "validated"
        assert "url_validation_date" in material

    @patch("src.extraction.validate_supplemental_urls.validate_url")
    def test_partial(self, mock_validate):
        mock_validate.side_effect = [("validated", None), ("broken", "404")]
        material = {"resource_urls": ["http://a.com", "http://b.com"]}
        validate_material_urls(material)
        assert material["url_validation_status"] == "partial"

    @patch("src.extraction.validate_supplemental_urls.validate_url")
    def test_all_broken(self, mock_validate):
        mock_validate.return_value = ("broken", "404")
        material = {"resource_urls": ["http://a.com"]}
        validate_material_urls(material)
        assert material["url_validation_status"] == "broken"

    def test_no_urls(self):
        material = {"resource_urls": []}
        validate_material_urls(material)
        assert material["url_validation_status"] == "no_urls"

    def test_missing_urls_key(self):
        material = {}
        validate_material_urls(material)
        assert material["url_validation_status"] == "no_urls"
