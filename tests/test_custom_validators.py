"""Tests for custom validators."""

import json
from pathlib import Path

import pytest

from src.utils.custom_validators import (
    ValidationError,
    validate_cross_reference,
    validate_data_with_custom_validators,
    validate_iso_date,
    validate_ulid,
    validate_url,
)


class TestULIDValidator:
    """Test ULID validation."""

    def test_valid_ulid(self):
        """Test valid ULID."""
        assert validate_ulid("01HQXYZ123456789ABCDEFGHJK")

    def test_invalid_ulid_length(self):
        """Test invalid ULID length."""
        with pytest.raises(ValidationError, match="Invalid ULID format"):
            validate_ulid("01HQXYZ123")

    def test_invalid_ulid_characters(self):
        """Test invalid ULID characters."""
        with pytest.raises(ValidationError, match="Invalid ULID format"):
            validate_ulid("01HQXYZ123456789ABCDEFGHIL")  # Contains I and L

    def test_lowercase_ulid(self):
        """Test lowercase ULID (invalid)."""
        with pytest.raises(ValidationError):
            validate_ulid("01hqxyz123456789abcdefghjk")


class TestDateValidator:
    """Test ISO date validation."""

    def test_valid_full_date(self):
        """Test valid full date."""
        assert validate_iso_date("1939-09-01")

    def test_valid_year_month(self):
        """Test valid year-month."""
        assert validate_iso_date("1939-09")

    def test_valid_year_only(self):
        """Test valid year only."""
        assert validate_iso_date("1939")

    def test_invalid_date_format(self):
        """Test invalid date format."""
        with pytest.raises(ValidationError, match="Invalid date format"):
            validate_iso_date("09/01/1939")

    def test_invalid_date_value(self):
        """Test invalid date value."""
        with pytest.raises(ValidationError):
            validate_iso_date("1939-13-01")  # Invalid month


class TestURLValidator:
    """Test URL validation."""

    def test_valid_http_url(self):
        """Test valid HTTP URL."""
        assert validate_url("http://example.com/path")

    def test_valid_https_url(self):
        """Test valid HTTPS URL."""
        assert validate_url("https://example.com/path")

    def test_invalid_url_no_scheme(self):
        """Test invalid URL without scheme."""
        with pytest.raises(ValidationError, match="Missing scheme"):
            validate_url("example.com")

    def test_invalid_url_scheme(self):
        """Test invalid URL scheme."""
        with pytest.raises(ValidationError, match="Must be http or https"):
            validate_url("ftp://example.com")


class TestCrossReferenceValidator:
    """Test cross-reference validation."""

    def test_cross_reference_found(self, tmp_path):
        """Test cross-reference found."""
        # Create test data
        people_dir = tmp_path / "people"
        people_dir.mkdir()

        person_data = {"PersonID": "01HQXYZ123456789ABCDEFGHJK", "name": "Test"}
        (people_dir / "person.json").write_text(json.dumps(person_data))

        # Validate
        assert validate_cross_reference(
            "01HQXYZ123456789ABCDEFGHJK", "PersonID", tmp_path
        )

    def test_cross_reference_not_found(self, tmp_path):
        """Test cross-reference not found."""
        people_dir = tmp_path / "people"
        people_dir.mkdir()

        with pytest.raises(ValidationError, match="not found"):
            validate_cross_reference("01HQXYZ123456789ABCDEFGHJK", "PersonID", tmp_path)

    def test_cross_reference_directory_not_exists(self, tmp_path):
        """Test cross-reference with non-existent directory."""
        result = validate_cross_reference(
            "01HQXYZ123456789ABCDEFGHJK", "PersonID", tmp_path
        )
        assert result is None  # Should skip validation


class TestDataValidator:
    """Test complete data validation."""

    def test_valid_data(self):
        """Test valid data."""
        data = {
            "PersonID": "01HQXYZ123456789ABCDEFGHJK",
            "name": "Test Person",
            "birth_date": "1920-01-01",
        }

        result = validate_data_with_custom_validators(data)
        assert len(result["errors"]) == 0

    def test_invalid_ulid(self):
        """Test invalid ULID."""
        data = {"PersonID": "invalid", "name": "Test"}

        result = validate_data_with_custom_validators(data)
        assert len(result["errors"]) > 0
        assert "PersonID" in result["errors"][0]

    def test_invalid_date(self):
        """Test invalid date."""
        data = {
            "PersonID": "01HQXYZ123456789ABCDEFGHJK",
            "birth_date": "01/01/1920",
        }

        result = validate_data_with_custom_validators(data)
        assert len(result["errors"]) > 0
        assert "birth_date" in result["errors"][0]

    def test_invalid_url(self):
        """Test invalid URL."""
        data = {
            "PersonID": "01HQXYZ123456789ABCDEFGHJK",
            "url": "http://",
        }

        result = validate_data_with_custom_validators(data)
        assert len(result["errors"]) > 0
        assert "url" in result["errors"][0]

    def test_multiple_errors(self):
        """Test multiple validation errors."""
        data = {
            "PersonID": "invalid",
            "birth_date": "invalid",
            "url": "http://",
        }

        result = validate_data_with_custom_validators(data)
        assert len(result["errors"]) >= 3
