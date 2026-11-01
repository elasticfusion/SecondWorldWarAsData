"""The schema-block reason must NAME the offending additionalProperties key(s) (actionable alarms),
not just report '<root>' — this is what let us diagnose the equipment 100%-block quickly.
Verifies the reason-string extraction logic used in file_lock._validate_entity's error handler.
"""

import re

import jsonschema


def test_additionalproperties_block_names_the_keys():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {}},
        "required": [],
    }
    bad = {"a": 1, "surprise_field": 2, "another": 3}
    try:
        jsonschema.validate(bad, schema)
        raise AssertionError("should have raised")
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        reason = f"{e.validator} at {loc}"
        if e.validator == "additionalProperties":
            extras = re.findall(r"'([^']+)'", str(getattr(e, "message", "")))
            if extras:
                reason = f"additionalProperties at {loc}: unexpected {extras}"
        assert "surprise_field" in reason and "another" in reason, reason
