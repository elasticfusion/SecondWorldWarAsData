"""Every entity-extraction prompt must instruct that CONJUNCTIONS denote SEPARATE entities.

Guards the extraction policy that 'X and Y', 'X or Y', and comma lists must each yield ONE
entity per item, never a single merged record. A regression (someone trimming the rule) is
caught here instead of silently re-merging conjoined entities at extraction time.
"""

from pathlib import Path

import pytest
import yaml

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Entity-extraction prompts that must carry the conjunction-splitting rule.
ENTITY_PROMPTS = [
    "events",
    "equipment_extract_broad",
    "dates",
    "logistics",
    "weather_batch",
    "people_groups",
    "places",
    "people",
    "casualties",
]


def _prompt_text(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("entity_type", ENTITY_PROMPTS)
def test_prompt_has_conjunction_rule(entity_type):
    """The prompt mentions the conjunction-splitting policy and 'separate' entities."""
    text = _prompt_text(entity_type).upper()
    assert "CONJUNCTION" in text, f"{entity_type}.yaml missing CONJUNCTION rule"
    assert "SEPARATE" in text, f"{entity_type}.yaml conjunction rule must say SEPARATE"


@pytest.mark.parametrize("entity_type", ENTITY_PROMPTS)
def test_prompt_still_valid_yaml(entity_type):
    """Adding the rule must not break YAML parsing (rule lives in a list/template)."""
    data = yaml.safe_load(_prompt_text(entity_type))
    assert isinstance(data, dict)
    assert "prompt_template" in data
    assert "schema" in data
