"""Regression: rendered equipment prompts must have NO leftover {placeholder}.

grok_client._validate_prompt raises ValueError if a rendered prompt still contains any {token}.
The multi-run refine prompt once embedded {item_name}/{item_original_text} inside its schema EXAMPLE
(not the instruction body); render_prompt leaves injected-schema tokens unsubstituted, so every
refine call threw ValueError and ALL items fell back (broad=109, fallbacks=109). This guard renders
each equipment prompt with its real kwargs and asserts no leftover placeholder remains.
"""

import re

from src.utils.prompt_loader import render_prompt

_LEFTOVER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def test_broad_prompt_no_leftover_placeholders():
    p = render_prompt("equipment_extract_broad", event_data='{"Event": {}}')
    assert not _LEFTOVER.findall(p), f"broad leftover: {set(_LEFTOVER.findall(p))}"


def test_refine_prompt_no_leftover_placeholders():
    p = render_prompt(
        "equipment_refine",
        item_name="tanks",
        item_original_text="the German tanks rolled forward",
        context="context window",
        theater_hint="D-Day",
    )
    assert not _LEFTOVER.findall(p), f"refine leftover: {set(_LEFTOVER.findall(p))}"
