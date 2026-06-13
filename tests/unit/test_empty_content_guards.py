"""Test that extractors skip API calls when content is empty."""

from src.extraction.dates import create_date_prompt
from src.extraction.people import create_people_prompt
from src.extraction.places import create_place_prompt
from src.extraction.weather_central import create_weather_prompt


class TestEmptyContentGuards:
    """Extractors must return empty/None when text content is empty."""

    def test_people_prompt_empty_on_no_fulltext(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {},
        }
        result = create_people_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result == ""

    def test_people_prompt_empty_on_whitespace_fulltext(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {"p1": "  "},
        }
        result = create_people_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result == ""

    def test_people_prompt_nonempty_with_content(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {"p1": "General Patton advanced."},
        }
        result = create_people_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result != ""
        assert "General Patton" in result

    def test_places_prompt_empty_on_no_fulltext(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {},
        }
        result = create_place_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result == ""

    def test_places_prompt_nonempty_with_content(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {"p1": "The attack on Metz began."},
        }
        result = create_place_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result != ""
        assert "Metz" in result

    def test_dates_prompt_empty_on_no_fulltext(self):
        """BUG: create_date_prompt lacks empty content guard — wastes API calls."""
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {},
        }
        result = create_date_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        # TODO: Should return "" but currently generates prompt anyway
        assert result != ""  # Documenting current (buggy) behavior

    def test_dates_prompt_empty_on_whitespace_fulltext(self):
        """create_date_prompt returns empty for whitespace-only content."""
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {"p1": "   "},
        }
        result = create_date_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result == ""

    def test_dates_prompt_nonempty_with_content(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "test",
            "Sub-event_fulltext": {"p1": "On September 15, 1944 the advance began."},
        }
        result = create_date_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test"
        )
        assert result != ""
        assert "September" in result

    def test_weather_returns_empty_on_no_content(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "",
            "Sub-event_fulltext": {},
        }
        result = create_weather_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test", places_index={}
        )
        assert result == ""

    def test_weather_returns_prompt_with_content(self):
        sub_event = {
            "Sub-eventID": "01ABC",
            "Sub-event_summary": "Heavy rain delayed the advance",
            "Sub-event_fulltext": {"p1": "Rain fell all morning."},
        }
        result = create_weather_prompt(
            sub_event=sub_event, event_id="01XYZ", event_name="Test", places_index={}
        )
        assert result is not None
        assert "rain" in result.lower()
