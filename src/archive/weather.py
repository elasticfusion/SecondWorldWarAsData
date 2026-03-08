"""Weather extraction from event data."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract all weather mentions from the provided event text.

Requirements:
- Link to PlaceMentionID and DateMentionID from previous extractions
- Extract temperature with units (celsius/fahrenheit)
- Note measurement system (metric/imperial)
- Describe notable impact on operations
- Reference any images showing weather conditions
- Preserve original text exactly

Return ONLY valid JSON. No additional text."""


def create_weather_prompt(
    event_data: Dict[str, Any], date_data: list, place_data: list
) -> list:
    """Create prompt for weather extraction."""
    event_name = event_data.get("Event", {})
    event_id = event_name.get("EventID", "")

    prompts = []

    for sub_event in event_name.get("Sub-events", []):
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_summary = sub_event.get("Sub-event_summary", "")
        fulltext = sub_event.get("Sub-event_fulltext", {})

        text = "\n".join(fulltext.values())

        # Find matching dates and places for this sub-event
        dates = [d for d in date_data if d.get("Sub-eventID") == sub_event_id]
        places = [p for p in place_data if p.get("Sub-eventID") == sub_event_id]

        prompt = f"""Extract weather mentions from this sub-event:

Sub-event: {sub_event_summary}
EventID: {event_id}
Sub-eventID: {sub_event_id}

Text:
{text}

Available Dates: {json.dumps(dates, indent=2)}
Available Places: {json.dumps(places, indent=2)}

Return JSON in this format:
{{
  "Event_Name": "Event name from context",
  "EventID": "{event_id}",
  "Sub-event_Name": "{sub_event_summary}",
  "Sub-eventID": "{sub_event_id}",
  "Weather_Mentions": [
    {{
      "WeatherMentionID": "01H8XYZC5AB123CD456EF789GH",
      "place_name": "Warsaw",
      "PlaceMentionID": "01H8XYZ8AB123CD456EF789GH",
      "date": "1939-09-01",
      "DateMentionID": "01H8XYZ3AB123CD456EF789GH",
      "weather_description": "Clear skies with light fog in early morning hours",
      "temperature": 15,
      "temperature_unit": "celsius",
      "measurement_system": "metric",
      "notable_impact": "Early morning fog provided limited concealment for initial German advance",
      "api_source": null,
      "image_references": [],
      "original_text": "The morning fog lifted by 0600 hours"
    }}
  ]
}}

Extract ALL weather mentions. Link to existing PlaceMentionID and DateMentionID. Generate valid ULIDs for WeatherMentionID.
"""
        prompts.append((sub_event_id, prompt))

    return prompts


def extract_weather(
    event_file: Path,
    date_file: Path,
    place_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
) -> Path:
    """Extract weather mentions from event file."""
    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    with open(date_file, "r", encoding="utf-8") as f:
        date_data = json.load(f)

    with open(place_file, "r", encoding="utf-8") as f:
        place_data = json.load(f)

    prompts = create_weather_prompt(event_data, date_data, place_data)
    all_weather = []

    for sub_event_id, prompt in prompts:
        response = grok_client.extract_json(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            use_cache=True,
            cache_type="weather",
        )

        all_weather.append(response)

    output_file = output_dir / event_file.name.replace("-event.json", "-weather.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_weather, f, indent=2, ensure_ascii=False)

    return output_file
