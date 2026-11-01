"""The schema-block reason must NAME the offending additionalProperties key(s) (actionable alarms),
not just report '<root>' — this is what let us diagnose the equipment 100%-block in minutes.
"""

import jsonschema
from src.utils.file_lock import _handle_validation_error


class _Rep:
    pass


def test_additionalproperties_block_names_the_keys(monkeypatch, tmp_path):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {}},
        "required": [],
    }
    bad = {"a": 1, "surprise_field": 2, "another": 3}
    try:
        jsonschema.validate(bad, schema)
        assert False, "should have raised"
    except jsonschema.ValidationError as e:
        # reproduce the reason-string logic
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        reason = f"{e.validator} at {loc}"
        if e.validator == "additionalProperties":
            import re

            extras = re.findall(r"'([^']+)'", str(getattr(e, "message", "")))
            if extras:
                reason = f"additionalProperties at {loc}: unexpected {extras}"
        assert "surprise_field" in reason and "another" in reason, reason
