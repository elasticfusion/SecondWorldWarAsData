"""An auto-created related-equipment stub is traceable to the source that names it."""

import json

from src.extraction import equipment as E

E_ID, S_ID, STUB = (
    "6H9CPGG5W72WS3K5R6CNPBNKX1",
    "7BQVGPE6S18KHTAAEGGWA47E84",
    "01M7HGQKZ94KYBXG8WP4QY3WF3",
)
TEXT = "neither the converted Maracaibo nor the first model LST proved satisfactory"


def _mention(text, **extra):
    return {
        "MentionID": "01M7HGQD6J2VE76J5HWMKJ0SCD",
        "EventID": E_ID,
        "Sub_eventID": S_ID,
        "Event_Name": "Outline Overlord Planning",
        "paragraph_numbers": [49],
        "original_text": text,
        "operating_country": "GBR",
        "quantity": 3,
        **extra,
    }


def test_a_stub_mention_keeps_the_location_not_the_relating_records_claims():
    src = _mention(TEXT, DateID="01M7GDR73VZ668JBKHVFDPQXGX")
    m = E._stub_mention(src, "the first model LST", "predecessor")
    assert (m["EventID"], m["paragraph_numbers"]) == (E_ID, [49])
    assert m["original_text"] == "the first model LST" and m["MentionID"]
    # named there, not asserted present: no sub-event, date, operator, quantity or assertion
    for k in (
        "Sub_eventID",
        "DateID",
        "operating_country",
        "quantity",
        "assertion_source",
    ):
        assert k not in m
    assert m["context"] == "named as related equipment (predecessor)"
    assert E._stub_mention(None, "x") is None and E._stub_mention({}, "x") is None


def _write(d, name, rec):
    (d / name).write_text(json.dumps(rec))


def test_backfill_repairs_a_stub_from_the_record_that_names_it(tmp_path):
    _write(
        tmp_path,
        "LST_1_P4QY3WF3.json",
        {"EquipmentID": STUB, "common_name": "LST (1)", "event_mentions": []},
    )
    _write(
        tmp_path,
        "Maracaibo_5ESTMNAP.json",
        {
            "EquipmentID": "01M7HGQKZ94KYBXG8W5ESTMNAP",
            "common_name": "Maracaibo",
            "event_mentions": [_mention("another sentence"), _mention(TEXT)],
            "related_equipment": [
                {
                    "relationship": "predecessor",
                    "name": "LST (1)",
                    "original_text": TEXT,
                    "EquipmentID": STUB,
                }
            ],
        },
    )
    _write(tmp_path, ".processed_events.json", "{}")
    assert E.backfill_stub_mentions(tmp_path) == 1
    stub = json.loads((tmp_path / "LST_1_P4QY3WF3.json").read_text())
    assert [m["original_text"] for m in stub["event_mentions"]] == [TEXT]
    assert E.backfill_stub_mentions(tmp_path) == 0  # idempotent


def test_an_unplaceable_stub_is_reported(tmp_path, monkeypatch):
    import src.utils.observe as O

    warned = []
    monkeypatch.setattr(O, "observe_warn", lambda *a, **k: warned.append(k))
    _write(
        tmp_path,
        "X_1.json",
        {"EquipmentID": STUB, "common_name": "X", "event_mentions": []},
    )
    _write(
        tmp_path,
        "Y_2.json",
        {
            "EquipmentID": "01M7HGQKZ94KYBXG8W5ESTMNAP",
            "event_mentions": [
                _mention("a"),
                _mention("b"),
            ],  # two, neither holds the text
            "related_equipment": [
                {"name": "X", "original_text": "zzz", "EquipmentID": STUB}
            ],
        },
    )
    assert E.backfill_stub_mentions(tmp_path) == 0
    assert warned and warned[0]["example"] == "X_1.json"


def test_a_new_stub_is_created_with_the_naming_mention(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(E, "_enrich_on_identity", lambda rec, client: None)
    rel = SimpleNamespace(
        name="LST (1)", relationship="predecessor", basis=None, original_text=TEXT
    )
    linked = E._link_related_equipment([rel], {}, tmp_path, None, _mention(TEXT))
    stub = json.loads(next(tmp_path.glob("LST*.json")).read_text())
    assert linked[0]["EquipmentID"] == stub["EquipmentID"]
    assert [(m["EventID"], m["original_text"]) for m in stub["event_mentions"]] == [
        (E_ID, TEXT)
    ]
