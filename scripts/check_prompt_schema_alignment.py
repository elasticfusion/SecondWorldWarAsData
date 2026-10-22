#!/usr/bin/env python3
"""Fail-closed check that each structured prompt's ``{schema}`` EXAMPLE is accepted by the
AUTHORITATIVE output schema in ``src/schemas``.

Why: the prompt's schema block is a HAND-WRITTEN example, maintained independently of the real
schema. When a schema field is added/changed, nothing mechanically forces the prompt to match — so
the prompt can drift (tell the model to emit a field the validator rejects, or omit a new field).
This check closes that gap: it validates the prompt's example (ULIDs filled, normalized the way the
pipeline normalizes the model's raw response) against the output schema and EXITS NON-ZERO on any
mismatch. Wired into scripts/deploy_all.sh as a hard stop, and exercised by
tests/test_prompt_schema_alignment.py.

Only prompts that map to a strict output schema are checked. Add new structured prompts to
``_CHECKS`` below.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Repo root on sys.path so `src.*` imports work when run standalone (deploy_all.sh / CLI).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from jsonschema import ValidationError, validate

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_ULID = "01HX7YZABCDEFGHJKMNPQRSTVW"


def _load_prompt_schema(name: str) -> dict:
    data = yaml.safe_load((_PROMPTS / f"{name}.yaml").read_text())
    return json.loads(data["schema"])


def _fill_ulids(obj: Any) -> Any:
    """Replace placeholder ULID-ish values so pattern validation passes (the check is about
    FIELD acceptance / shape, not the literal placeholder text)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if (k.endswith("ID") or k.endswith("MentionID")) and isinstance(v, str):
                out[k] = _ULID
            else:
                out[k] = _fill_ulids(v)
        return out
    if isinstance(obj, list):
        return [_fill_ulids(x) for x in obj]
    return obj


# --- per-prompt normalizers: turn the prompt EXAMPLE into the instance the output schema sees ---


def _norm_events(ex: dict) -> dict:
    # Prompt returns a FLAT event; the code wraps it in {"Event": ...} + converts paragraphs.
    inst = {"Event": ex} if "EventID" in ex and "Event" not in ex else ex
    for se in inst.get("Event", {}).get("Sub-events", []):
        se.pop("paragraph_numbers", None)
        se.pop("references", None)
        se.setdefault("Sub-event_fulltext", {"Paragraph_1": "text"})
    return _fill_ulids(inst)


def _norm_people(ex: dict) -> dict:
    # Prompt returns {"People":[person,...]}; the output schema validates ONE persisted person.
    return _fill_ulids(ex["People"][0])


def _norm_passthrough(ex: dict) -> dict:
    return _fill_ulids(ex)


# prompt name -> (schema "module:ATTR", normalizer). Only structured prompts with a strict schema.
_CHECKS: Dict[str, Tuple[str, Callable[[dict], dict]]] = {
    "events": ("src.schemas.events_output:EVENTS_OUTPUT_SCHEMA", _norm_events),
    "people": ("src.schemas.people_output:PEOPLE_OUTPUT_SCHEMA", _norm_people),
}


def _import_schema(path: str) -> dict:
    mod_name, attr = path.split(":")
    import importlib

    return getattr(importlib.import_module(mod_name), attr)


def check() -> List[str]:
    """Return a list of alignment errors (empty == aligned)."""
    errors: List[str] = []
    for name, (schema_path, norm) in _CHECKS.items():
        p = _PROMPTS / f"{name}.yaml"
        if not p.exists():
            errors.append(f"{name}: prompt file missing")
            continue
        try:
            example = _load_prompt_schema(name)
        except (json.JSONDecodeError, KeyError, yaml.YAMLError) as e:
            errors.append(f"{name}: prompt schema not loadable JSON ({e})")
            continue
        try:
            schema = _import_schema(schema_path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: output schema {schema_path} not importable ({e})")
            continue
        try:
            validate(instance=norm(example), schema=schema)
        except ValidationError as e:
            loc = "/".join(str(x) for x in e.absolute_path) or "<root>"
            errors.append(
                f"{name}: prompt example NOT accepted by {schema_path.split(':')[1]} "
                f"at '{loc}': {e.message}"
            )
        except Exception as e:  # noqa: BLE001 - normalizer shape error
            errors.append(f"{name}: normalization/validation error: {e}")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    errors = check()
    if errors:
        print("  \u2717 Prompt\u2194schema ALIGNMENT failed:")
        for e in errors:
            print(f"    {e}")
        return 1
    print(
        f"  Prompt\u2194schema alignment OK ({len(_CHECKS)} structured prompts validated)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
