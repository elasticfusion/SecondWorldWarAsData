"""Guard: CASUALTY_ITEM_SCHEMA must accept every field prompts/casualties.yaml tells the model to
emit, or additionalProperties:False silently drops the whole item.

Chapter-8 full run: casualties produced=0 against a deterministic floor of 684 — the model DID
return items (chunk.summary items_in=4/3/5/3) but every one was 'Dropping invalid casualty item:
cause, original_text do not match ^_' because the item schema's properties omitted cause/
original_text (and the type enum lacked non_battle). This guard validates representative emitted
items so the schema can't drift narrow again.
"""

import pytest
from jsonschema import ValidationError, validate

from src.json_schemas import CASUALTY_ITEM_SCHEMA


@pytest.mark.parametrize(
    "item",
    [
        {
            "type": "casualties",
            "description": "heavy losses",
            "original_text": "the battalion lost 45 killed",
            "side": "allied",
        },
        {
            "type": "non_battle",
            "cause": "weather_exposure",
            "description": "frostbite cases",
            "original_text": "frostbite mounted",
        },
        {
            "type": "casualties",
            "description": "heavy casualties",
            "magnitude": "heavy",
            "is_comparative": True,
            "original_text": "heavy",
        },
        {
            "type": "killed",
            "description": "civilians",
            "side": "neutral",
            "original_text": "civilians killed",
        },
    ],
)
def test_prompt_emitted_casualty_item_validates(item):
    validate(instance=item, schema=CASUALTY_ITEM_SCHEMA)  # must not raise


def test_cause_and_original_text_are_accepted_properties():
    props = CASUALTY_ITEM_SCHEMA["properties"]
    assert "cause" in props, "cause missing -> items with a cause are silently dropped"
    assert (
        "original_text" in props
    ), "original_text (provenance) missing -> sourced items dropped"


def test_type_enum_includes_non_battle():
    assert "non_battle" in CASUALTY_ITEM_SCHEMA["properties"]["type"]["enum"]


def test_truly_unknown_key_still_rejected():
    # additionalProperties:False still guards genuine garbage (non-underscore unknown key).
    with pytest.raises(ValidationError):
        validate(
            instance={"type": "killed", "description": "x", "bogus_field": 1},
            schema=CASUALTY_ITEM_SCHEMA,
        )
