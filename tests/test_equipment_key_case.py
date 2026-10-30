"""Regression: _extract_equipment_with_llm must accept the 'Equipment' (capital E) wrapper key
that the prompt schema + model actually emit — a case mismatch previously discarded the ENTIRE
equipment list (dict fell through isinstance(list) -> None -> 'No equipment extracted'), yielding
0 equipment on an equipment-dense chapter (XChannel-8)."""

from unittest.mock import MagicMock
from src.extraction.equipment import _extract_equipment_with_llm


def _grok_returning(payload):
    g = MagicMock()
    g.extract_json.return_value = payload
    return g


def test_capital_equipment_key_is_unwrapped():
    payload = {
        "Equipment": [
            {"name": "LST", "category": "naval", "assertion_source": "narrative"}
        ]
    }
    out = _extract_equipment_with_llm(
        {"EventID": "E1"}, _grok_returning(payload), max_retries=1
    )
    assert isinstance(out, list) and len(out) == 1 and out[0]["name"] == "LST"


def test_lowercase_equipment_key_still_works():
    payload = {
        "equipment": [
            {"name": "DUKW", "category": "vehicles", "assertion_source": "narrative"}
        ]
    }
    out = _extract_equipment_with_llm(
        {"EventID": "E1"}, _grok_returning(payload), max_retries=1
    )
    assert isinstance(out, list) and len(out) == 1 and out[0]["name"] == "DUKW"


def test_bare_list_still_works():
    payload = [
        {"name": "Jeep", "category": "vehicles", "assertion_source": "narrative"}
    ]
    out = _extract_equipment_with_llm(
        {"EventID": "E1"}, _grok_returning(payload), max_retries=1
    )
    assert isinstance(out, list) and len(out) == 1
