"""Tests for src/extraction/weather_central.py."""

# pylint: disable=missing-function-docstring

import json

import pytest

from src.extraction.weather_central import (
    _build_date_id_lookup,
    _build_dates_section,
    _filter_invalid_weather,
    _is_valid_weather_mention,
    _normalize_weather_key,
)


class TestIsValidWeatherMention:
    def test_valid_mention(self):
        mention = {
            "date": "1944-09-15",
            "weather_description": "Heavy rain",
            "original_text": "It rained heavily on September 15",
        }
        valid, reason = _is_valid_weather_mention(mention)
        assert valid is True
        assert reason == ""

    def test_rejects_missing_date(self):
        mention = {"weather_description": "Rain", "original_text": "text"}
        valid, reason = _is_valid_weather_mention(mention)
        assert valid is False
        assert "null date" in reason

    def test_rejects_approximate_date(self):
        mention = {
            "date": "1944-09",
            "weather_description": "Rain",
            "original_text": "text",
        }
        valid, reason = _is_valid_weather_mention(mention)
        assert valid is False
        assert "approximate" in reason

    def test_rejects_missing_description(self):
        mention = {"date": "1944-09-15", "original_text": "text"}
        valid, reason = _is_valid_weather_mention(mention)
        assert valid is False
        assert "null description" in reason

    def test_rejects_missing_original_text(self):
        mention = {"date": "1944-09-15", "weather_description": "Rain"}
        valid, reason = _is_valid_weather_mention(mention)
        assert valid is False
        assert "null original_text" in reason

    def test_rejects_non_dict(self):
        valid, reason = _is_valid_weather_mention("not a dict")
        assert valid is False


class TestFilterInvalidWeather:
    def test_removes_invalid_keeps_valid(self):
        data = {
            "Weather_Mentions": [
                {
                    "date": "1944-09-15",
                    "weather_description": "Rain",
                    "original_text": "It rained",
                },
                {"date": "", "weather_description": "Snow", "original_text": "snowed"},
            ]
        }
        result = _filter_invalid_weather(data)
        assert len(result["Weather_Mentions"]) == 1
        assert result["Weather_Mentions"][0]["date"] == "1944-09-15"

    def test_handles_missing_key(self):
        data = {"Event_Name": "Test"}
        result = _filter_invalid_weather(data)
        assert "Weather_Mentions" not in result


class TestNormalizeWeatherKey:
    def test_creates_key(self):
        key = _normalize_weather_key("1944-09-15", "Nancy")
        assert key == "1944-09-15_Nancy"

    def test_replaces_spaces(self):
        key = _normalize_weather_key("1944-06-06", "Omaha Beach")
        assert key == "1944-06-06_Omaha_Beach"


class TestBuildDateIdLookup:
    def test_builds_lookup(self, tmp_path):
        dates_dir = tmp_path / "dates"
        dates_dir.mkdir()
        (dates_dir / "d1.json").write_text(
            json.dumps({"DateID": "01DATE1", "date_start": "1944-09-15"}),
            encoding="utf-8",
        )
        (dates_dir / "d2.json").write_text(
            json.dumps({"DateID": "01DATE2", "date_start": "1944-09-16"}),
            encoding="utf-8",
        )
        (dates_dir / "index.json").write_text("{}", encoding="utf-8")

        lookup = _build_date_id_lookup(dates_dir)
        assert lookup["1944-09-15"] == "01DATE1"
        assert lookup["1944-09-16"] == "01DATE2"
        assert "index.json" not in str(lookup)

    def test_returns_empty_if_missing(self, tmp_path):
        assert _build_date_id_lookup(tmp_path / "nope") == {}

    def test_skips_corrupted_files(self, tmp_path):
        dates_dir = tmp_path / "dates"
        dates_dir.mkdir()
        (dates_dir / "bad.json").write_text("not json", encoding="utf-8")
        assert _build_date_id_lookup(dates_dir) == {}


class TestBuildDatesSection:
    def test_builds_dates_list(self):
        sub_event = {
            "Dates": [
                {"date_start": "1944-09-15", "DateMentionID": "DM1"},
                {"date_start": "1944-09-16", "DateMentionID": "DM2"},
            ]
        }
        result = _build_dates_section(sub_event)
        assert "1944-09-15" in result
        assert "DM1" in result

    def test_returns_empty_if_no_dates(self):
        assert _build_dates_section({}) == ""
        assert _build_dates_section({"Dates": []}) == ""
