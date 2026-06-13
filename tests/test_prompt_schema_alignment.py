"""Test that prompt schema examples align with output validation schemas.

Catches mismatches where the prompt tells the AI to return one format
but the validator expects another (e.g., flat vs wrapped structure).
"""

import json

import pytest
import yaml
from jsonschema import validate, ValidationError
from pathlib import Path

from src.schemas.events_output import EVENTS_OUTPUT_SCHEMA

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

ALL_PROMPT_TYPES = [
    "events",
    "people",
    "places",
    "dates",
    "equipment",
    "casualties",
    "logistics",
    "weather",
]

# These prompts have malformed schema fields (known issues to fix)
_BROKEN_SCHEMA_PROMPTS: set = set()


def _load_prompt_schema(name: str) -> dict:
    """Load the schema example from a prompt YAML file."""
    path = PROMPTS_DIR / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return json.loads(data["schema"])


def _make_valid_ulid() -> str:
    return "01HX7YZABCDEFGHJKMNPQRSTVW"


class TestPromptSchemaExists:
    """Every prompt YAML has a schema field that parses as valid JSON."""

    @pytest.mark.parametrize("entity_type", ALL_PROMPT_TYPES)
    def test_prompt_schema_exists_and_parses(self, entity_type):
        if entity_type in _BROKEN_SCHEMA_PROMPTS:
            pytest.xfail(f"{entity_type}.yaml has malformed schema field")
        schema = _load_prompt_schema(entity_type)
        assert isinstance(schema, dict)

    @pytest.mark.parametrize("entity_type", ALL_PROMPT_TYPES)
    def test_prompt_has_required_fields(self, entity_type):
        """Every prompt YAML must have prompt_template and schema."""
        path = PROMPTS_DIR / f"{entity_type}.yaml"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert "prompt_template" in data, f"{entity_type} missing prompt_template"
        assert "schema" in data, f"{entity_type} missing schema"


class TestEventPromptAlignment:
    """Verify event prompt example produces valid output after wrapping."""

    def test_raw_prompt_example_needs_wrapping(self):
        """The prompt example is flat — verify our wrapping normalizes it."""
        example = _load_prompt_schema("events")
        # Prompt shows flat format
        assert "EventID" in example
        assert "Event" not in example

    def test_wrapped_prompt_example_validates(self):
        """After wrapping, the prompt example should pass the output schema."""
        example = _load_prompt_schema("events")
        # Apply the same wrapping the code does
        if "EventID" in example and "Event" not in example:
            example = {"Event": example}

        # Fill in valid ULIDs (prompt has placeholders)
        example["Event"]["EventID"] = _make_valid_ulid()
        for se in example["Event"].get("Sub-events", []):
            se["Sub-eventID"] = _make_valid_ulid()
            # Convert paragraph_numbers to Sub-event_fulltext (as code does)
            se.pop("paragraph_numbers", None)
            se.pop("references", None)
            se["Sub-event_fulltext"] = {"Paragraph_1": "Sample text"}

        # Must validate without error
        validate(instance=example, schema=EVENTS_OUTPUT_SCHEMA)

    def test_flat_response_would_fail_without_wrapping(self):
        """Confirm that the flat format the AI returns FAILS validation directly."""
        example = _load_prompt_schema("events")
        example["EventID"] = _make_valid_ulid()
        for se in example.get("Sub-events", []):
            se["Sub-eventID"] = _make_valid_ulid()
            se.pop("paragraph_numbers", None)
            se.pop("references", None)
            se["Sub-event_fulltext"] = {"Paragraph_1": "text"}

        with pytest.raises(ValidationError):
            validate(instance=example, schema=EVENTS_OUTPUT_SCHEMA)
