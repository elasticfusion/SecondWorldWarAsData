"""Regression — logistics §1.0b WARNING fixes.

(1) _save_extraction added the dedup key BEFORE the write, so a write/validate failure poisoned the
    key and a later identical extraction was silently dropped -> fixed: add only after a confirmed
    write.
(2) _rebuild_index keyed on the stem minus the ULID, collapsing DISTINCT sub-events that share
    category_type_date into one index entry -> fixed: key on the full (ULID-unique) stem.
(3) _build_weather_impact matched with a bare `desc in weather_desc` substring (e.g. "rain" linked
    "terrain") -> fixed: normalized exact / word-bounded match.
"""

import json
from unittest import mock

from src.extraction import logistics as L


# --- (1) dedup key not poisoned on write failure ------------------------------------------
def test_save_extraction_write_failure_does_not_poison_dedup_key(tmp_path):
    ext = mock.Mock()
    ext.category = "general"
    ext.type = "supply_shortage"
    ext.date_start = "1944-06-06"
    sub_event = {"Sub-eventID": "01SUB0000000000000000000A0"}
    existing = set()
    indexes = ({}, {}, {}, {}, {}, {})

    # First attempt: write raises -> returns False, key MUST NOT be in existing_keys.
    with mock.patch(
        "src.utils.file_lock.write_json_with_lock", side_effect=OSError("disk full")
    ):
        with (
            mock.patch.object(
                L,
                "_build_logistics_data",
                return_value={
                    "category": "general",
                    "logistics_type": "supply_shortage",
                    "LogisticsID": "01LOG0000000000000000000A0",
                },
            ),
            mock.patch.object(
                L.Logistics,
                "model_validate",
                lambda d: mock.Mock(model_dump=lambda **k: d),
            ),
        ):
            ok = L._save_extraction(
                ext, {"EventID": "E"}, sub_event, indexes, tmp_path, existing
            )
    assert ok is False
    assert not existing  # NOT poisoned -> a retry can still save

    # Second attempt (write succeeds): now it saves and the key is recorded.
    with mock.patch("src.utils.file_lock.write_json_with_lock") as w:
        with (
            mock.patch.object(
                L,
                "_build_logistics_data",
                return_value={
                    "category": "general",
                    "logistics_type": "supply_shortage",
                    "LogisticsID": "01LOG0000000000000000000A0",
                },
            ),
            mock.patch.object(
                L.Logistics,
                "model_validate",
                lambda d: mock.Mock(model_dump=lambda **k: d),
            ),
        ):
            ok = L._save_extraction(
                ext, {"EventID": "E"}, sub_event, indexes, tmp_path, existing
            )
    assert ok is True
    assert len(existing) == 1
    w.assert_called_once()


# --- (2) index does not collapse distinct records ----------------------------------------
def test_rebuild_index_keeps_distinct_same_prefix_records(tmp_path):
    # Two records sharing category_type_date but DIFFERENT ULID suffixes.
    (tmp_path / "general_supply_shortage_19440606_AAAAAAAA.json").write_text("{}")
    (tmp_path / "general_supply_shortage_19440606_BBBBBBBB.json").write_text("{}")
    L._rebuild_index(tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) == 2  # both preserved (old code collapsed to 1)
    assert set(index.values()) == {
        "general_supply_shortage_19440606_AAAAAAAA.json",
        "general_supply_shortage_19440606_BBBBBBBB.json",
    }


# --- (3) weather match is bounded, not bare substring ------------------------------------
def test_build_weather_impact_no_false_substring():
    # index desc "rain" must NOT match "terrain was difficult" (bare-substring bug)
    out = L._build_weather_impact(
        "terrain was difficult", "medium", {"rain": "01WEA0000000000000000RAIN"}
    )
    assert out is None


def test_build_weather_impact_exact_and_bounded_match():
    # exact normalized match links
    out = L._build_weather_impact(
        "Heavy Rain", "high", {"heavy rain": "01WEA000000000000000HRAIN"}
    )
    assert out and out["WeatherID"] == "01WEA000000000000000HRAIN"
    # word-bounded containment also links ("rain" as a whole word in a longer phrase)
    out = L._build_weather_impact(
        "it was heavy rain today", "high", {"heavy rain": "01WEA000000000000000HRAIN"}
    )
    assert out and out["WeatherID"] == "01WEA000000000000000HRAIN"


def test_build_entity_index_reads_weather_nested_description(tmp_path):
    """WARNING: weather stores its description under extracted_data.description (no top-level
    'description'), so the index must read the nested path — else weather_index is always {} and the
    logistics weather-impact link is dead."""
    wdir = tmp_path / "weather"
    wdir.mkdir()
    (wdir / "w1.json").write_text(
        json.dumps(
            {
                "WeatherID": "01WEA0000000000000000001",
                "extracted_data": {"description": "heavy rain"},
            }
        )
    )
    # top-level-only read (old behavior) -> empty
    assert L._build_entity_index(tmp_path, "weather", "WeatherID", "description") == {}
    # nested read (fix) -> populated
    idx = L._build_entity_index(
        tmp_path,
        "weather",
        "WeatherID",
        "description",
        nested_path="extracted_data.description",
    )
    assert idx == {"heavy rain": "01WEA0000000000000000001"}
    # and the link now works end-to-end
    out = L._build_weather_impact("heavy rain", "high", idx)
    assert out and out["WeatherID"] == "01WEA0000000000000000001"


def test_build_entity_index_skips_non_dict_file(tmp_path):
    """A non-dict top-level JSON in the dir must be SKIPPED, not abort the whole index build."""
    wdir = tmp_path / "weather"
    wdir.mkdir()
    (wdir / "bad.json").write_text(json.dumps(["not", "a", "dict"]))
    (wdir / "good.json").write_text(
        json.dumps(
            {
                "WeatherID": "01WEA0000000000000000002",
                "extracted_data": {"description": "fog"},
            }
        )
    )
    idx = L._build_entity_index(
        tmp_path,
        "weather",
        "WeatherID",
        "description",
        nested_path="extracted_data.description",
    )
    assert idx == {
        "fog": "01WEA0000000000000000002"
    }  # good one indexed; bad one skipped
