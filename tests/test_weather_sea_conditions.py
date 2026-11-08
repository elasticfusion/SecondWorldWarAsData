"""Weather 2.25 (XChannel-8, 2026-11-07): an amphibious chapter's weather is wind, sea state and
tide; a forecast or a planning requirement is not observed weather; every mention keeps its own
statement; a mention is never dropped for lacking an exact date."""

import json

import pytest

from src.extraction import weather_central as wc
from src.schemas.weather_output import WEATHER_OUTPUT_SCHEMA

SEID = "01M6W8ZCHP9A3NYBQSXBHDN9G3"
SEID2 = "01M6W8ZCHP9A3NYBQSXBHDN9G5"
EVENT_ID = "01M6W8ZCHP9A3NYBQSXBHDN9G4"


class _Grok:
    batch_mode = False

    def __init__(self, reply):
        self.reply = reply

    def extract_json(self, prompt, **_k):
        return self.reply


def _run(tmp_path, reply, monkeypatch):
    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    content = tmp_path / "content" / "B"
    content.mkdir(parents=True)
    sub_events = [
        {
            "Sub-eventID": sid,
            "Sub-event_summary": f"S{i}",
            "Sub-event_fulltext": {"1": "x"},
        }
        for i, sid in enumerate((SEID, SEID2))
    ]
    event_file = content / "c-event.json"
    event_file.write_text(
        json.dumps(
            {"Chapter": "C", "Event": {"EventID": EVENT_ID, "Sub-events": sub_events}}
        ),
        encoding="utf-8",
    )
    parsed = content / "c-parsed.json"
    parsed.write_text(json.dumps({"book": "B", "author": "A"}), encoding="utf-8")
    wdir = tmp_path / "weather"
    wdir.mkdir()
    wc.extract_weather_central(
        event_file, wdir, _Grok(reply), tmp_path / "places", parsed_file=parsed
    )
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(wdir.glob("*.json"))
        if f.name not in ("index.json", ".processed_events.json")
    ]


def _mention(**kw):
    base = {"place_name": "Omaha Beach", "date": "1944-06-06", "original_text": "t"}
    return {**base, **kw}


def test_sea_state_only_mention_is_kept_with_its_fields(tmp_path, monkeypatch):
    reply = {
        SEID: [
            _mention(
                sea_state_text="heavy seas",
                wind_text="gusty wind 15-20 knots",
                wind_speed_low=15,
                wind_speed_high="20",
                wind_speed_unit="kts",
                tide_text="a rising tide",
            )
        ]
    }
    (rec,) = _run(tmp_path, reply, monkeypatch)
    ext = rec["extracted_data"]
    assert ext["sea_state_text"] == "heavy seas" and ext["tide_text"] == "a rising tide"
    assert (ext["wind_speed_low"], ext["wind_speed_high"], ext["wind_speed_unit"]) == (
        15.0,
        20.0,
        "knots",
    )
    assert ext["observation_type"] == "observed"


def test_a_wind_number_without_a_unit_is_not_a_stated_speed(tmp_path, monkeypatch):
    reply = {SEID: [_mention(wind_text="strong wind", wind_speed_low=15)]}
    (rec,) = _run(tmp_path, reply, monkeypatch)
    assert rec["extracted_data"]["wind_speed_low"] is None


def test_a_forecast_is_never_merged_into_observed_weather(tmp_path, monkeypatch):
    reply = {
        SEID: [
            _mention(
                weather_description="forecast: wind force 5",
                observation_type="forecast",
            )
        ],
        SEID2: [_mention(weather_description="heavy seas")],
    }
    recs = _run(tmp_path, reply, monkeypatch)
    kinds = sorted(r["extracted_data"]["observation_type"] for r in recs)
    assert kinds == ["forecast", "observed"]  # two records for the same place and date


def test_every_mention_keeps_its_own_statement(tmp_path, monkeypatch):
    reply = {
        SEID: [
            _mention(
                weather_description="choppy waters", original_text="Waters were choppy."
            )
        ],
        SEID2: [
            _mention(
                weather_description="heavy surf", original_text="The surf was heavy."
            )
        ],
    }
    (rec,) = _run(tmp_path, reply, monkeypatch)
    texts = {m["original_text"] for m in rec["event_mentions"]}
    assert texts == {"Waters were choppy.", "The surf was heavy."}


def test_an_undated_mention_is_kept(tmp_path, monkeypatch):
    reply = {SEID: [_mention(date=None, weather_description="heavy seas")]}
    recs = _run(tmp_path, reply, monkeypatch)
    assert len(recs) == 1 and recs[0]["event_mentions"]


def test_records_validate_against_the_schema(tmp_path, monkeypatch):
    import jsonschema

    reply = {
        SEID: [
            _mention(
                observation_type="planning_requirement",
                light_text="a late-rising moon",
                tide_text="low tide at dawn",
            )
        ]
    }
    for rec in _run(tmp_path, reply, monkeypatch):
        jsonschema.validate(rec, WEATHER_OUTPUT_SCHEMA)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("forecast", "forecast"),
        ("Planning requirement", "planning_requirement"),
        (None, "observed"),
        ("x", "observed"),
    ],
)
def test_observation_type_normalization(raw, expected):
    assert wc._observation_type(raw) == expected


def test_no_archive_lookup_without_an_exact_date(monkeypatch):
    called = []
    monkeypatch.setattr(wc, "recorded_fetch", lambda *a, **k: called.append(a))
    assert wc._fetch_weather_from_api("", 49.3, -0.8) is None
    assert wc._fetch_weather_from_api("1944-06", 49.3, -0.8) is None
    assert called == []


# --- long sub-events are read in windows (XChannel-8: 143K / 83K-char sub-events) -------------


def test_long_text_is_split_on_paragraphs():
    paras = {f"P{i}": "x" * 5000 for i in range(5)}
    windows = wc._text_windows(paras)
    assert len(windows) == 3 and all(len(w) <= wc.WINDOW_CHARS for w in windows)
    assert "".join(windows).count("x") == 25000  # nothing lost


def test_an_oversized_paragraph_is_its_own_window_never_cut():
    windows = wc._text_windows({"a": "y" * (wc.WINDOW_CHARS + 10), "b": "z"})
    assert windows == ["y" * (wc.WINDOW_CHARS + 10), "z"]


def test_calls_are_packed_by_size_and_count():
    blocks = ["b" * 20_000, "b" * 20_000, "s", "s", "s"]
    chunks = wc._pack_blocks(blocks, max_blocks=2)
    assert [len(c) for c in chunks] == [1, 2, 2]


def test_windows_of_one_sub_event_accumulate():
    from src.utils.chunked_extract import extend_lists, extract_with_chunk_halving

    replies = iter([{SEID: [{"n": 1}]}, {SEID: [{"n": 2}]}])
    out = extract_with_chunk_halving(
        [["w1"], ["w2"]], lambda chunk: next(replies), "weather", merge=extend_lists
    )
    assert out == {SEID: [{"n": 1}, {"n": 2}]}  # the default update would keep only n=2


def test_every_window_reaches_grok(tmp_path, monkeypatch):
    prompts = []

    class _G(_Grok):
        def extract_json(self, prompt, **_k):
            prompts.append(prompt)
            return {}

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    se = {
        "Sub-eventID": SEID,
        "Sub-event_summary": "Long",
        "Sub-event_fulltext": {f"P{i}": f"para{i} " + "x" * 9000 for i in range(4)},
    }
    wc._batch_extract_weather([se], EVENT_ID, "C", _G({}), 1)
    joined = "".join(prompts)
    assert all(f"para{i}" in joined for i in range(4))
    assert len(prompts) >= 2 and "(part 1 of" in joined


def test_two_statements_of_one_sub_event_are_both_kept(tmp_path, monkeypatch):
    reply = {
        SEID: [
            _mention(
                weather_description="rising tide", original_text="The tide rose fast."
            ),
            _mention(
                weather_description="heavy seas", original_text="Seas were heavy."
            ),
        ]
    }
    (rec,) = _run(tmp_path, reply, monkeypatch)
    assert len(rec["event_mentions"]) == 2


def test_one_statement_in_two_overlapping_sub_events_counts_once(tmp_path, monkeypatch):
    same = _mention(
        weather_description="rising tide", original_text="The tide rose fast."
    )
    (rec,) = _run(tmp_path, {SEID: [dict(same)], SEID2: [dict(same)]}, monkeypatch)
    assert len(rec["event_mentions"]) == 1


# --- review hardening --------------------------------------------------------------------------


def test_exhausted_retries_raise_instead_of_reading_as_no_weather():
    class _Down:
        def extract_json(self, *a, **k):
            raise RuntimeError("503")

    with pytest.raises(wc.WeatherCallFailed):
        wc._call_and_parse_weather(_Down(), "p", 2)


def test_a_failed_window_keeps_the_answered_ones_and_leaves_the_event_unprocessed(
    tmp_path, monkeypatch
):
    calls = {"n": 0}

    class _Flaky(_Grok):
        def extract_json(self, prompt, **_k):
            calls["n"] += 1
            if "para0" in prompt:
                return {
                    SEID: [
                        _mention(
                            weather_description="heavy seas", original_text="para0"
                        )
                    ]
                }
            raise RuntimeError("503")

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    content = tmp_path / "content" / "B"
    content.mkdir(parents=True)
    se = {
        "Sub-eventID": SEID,
        "Sub-event_summary": "Long",
        "Sub-event_fulltext": {f"P{i}": f"para{i} " + "x" * 20_000 for i in range(2)},
    }
    event_file = content / "c-event.json"
    event_file.write_text(
        json.dumps({"Chapter": "C", "Event": {"EventID": EVENT_ID, "Sub-events": [se]}})
    )
    parsed = content / "c-parsed.json"
    parsed.write_text(json.dumps({"book": "B", "author": "A"}))
    wdir = tmp_path / "weather"
    wdir.mkdir()
    with pytest.raises(wc.WeatherCallFailed):
        wc.extract_weather_central(
            event_file,
            wdir,
            _Flaky({}),
            tmp_path / "places",
            parsed_file=parsed,
            max_retries=1,
        )
    assert str(event_file) not in wc._load_weather_processed(wdir)  # retried next run
    saved = [f for f in wdir.glob("*.json") if f.name not in ("index.json",)]
    assert saved  # the answered window's statement was kept


def test_an_error_resolved_later_in_the_run_raises_no_alert(tmp_path, caplog):
    import logging

    import phase2_extract as P

    P._OPTIONAL_ERRORS.clear()
    try:
        P._observe_optional_error(
            logging.getLogger("t"), "weather", "ch1-event.json", "boom"
        )
        P._observe_optional_ok(
            "weather", "ch1-event.json"
        )  # the retrieve pass succeeded
        with caplog.at_level(logging.INFO):
            P._log_phase2_summary(tmp_path / "output", logging.getLogger("t"))
        assert not any("optional_failed" in r.getMessage() for r in caplog.records)
    finally:
        P._OPTIONAL_ERRORS.clear()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("15", 15.0),
        (12, 12.0),
        ("nan", None),
        ("inf", None),
        (-3, None),
        ("15-20", None),
        (True, None),
    ],
)
def test_stated_numbers(value, expected):
    assert wc._stated_number(value) == expected


def test_temperatures_may_be_negative_and_zero_is_kept():
    assert wc._stated_number(-5, allow_negative=True) == -5.0
    assert wc._extracted_data({"temperature": 0}, "B", "A")["temperature"] == 0.0


def test_wind_unit_alone_and_reversed_range():
    assert wc._condition_fields({"wind_speed_unit": "knots"})["wind_speed_unit"] is None
    f = wc._condition_fields(
        {"wind_speed_low": 20, "wind_speed_high": 15, "wind_speed_unit": "kt"}
    )
    assert (f["wind_speed_low"], f["wind_speed_high"]) == (15.0, 20.0)


@pytest.mark.parametrize("text", ["<as written, or null>", "null", "N/A", "  ", None])
def test_placeholders_are_not_stored_as_text(text):
    assert wc._clean_text(text) is None
    assert wc._condition_fields({"sea_state_text": text})["sea_state_text"] is None


@pytest.mark.parametrize(
    "value", ["<YYYY-MM-DD or null>", "early June", "1944-06", None]
)
def test_inexact_dates_are_undated(value):
    assert wc._exact_date(value) == ""


def test_a_wind_direction_alone_is_kept():
    assert wc._normalize_weather_mention({"wind_direction": "NW"}) is not None


def test_undated_statements_of_different_events_get_their_own_records(tmp_path):
    index: dict = {}
    files = {
        wc._find_or_create_weather(
            {"place_name": "the Channel", "date": None},
            tmp_path,
            index,
            tmp_path / "p",
            False,
            {},
            ev,
        )
        for ev in ("01M6W8ZCHP9A3NYBQSXBHDN9G4", "01M6W8ZCHP9A3NYBQSXBHDN9G6")
    }
    assert len(files) == 2


def test_filenames_use_the_random_ulid_tail(tmp_path, monkeypatch):
    """Two records created in the same millisecond share the ULID timestamp prefix: the name
    must come from the random tail, or the second write overwrites the first."""
    ids = iter(["01M6W8ZC" + "A" * 18, "01M6W8ZC" + "B" * 18])
    monkeypatch.setattr(wc.ulid, "new", lambda: next(ids))
    index: dict = {}
    a = wc._find_or_create_weather(
        {"place_name": "Omaha", "date": "1944-06-06"},
        tmp_path,
        index,
        tmp_path / "p",
        False,
        {},
    )
    b = wc._find_or_create_weather(
        {"place_name": "Omaha", "date": "1944-06-06", "observation_type": "forecast"},
        tmp_path,
        index,
        tmp_path / "p",
        False,
        {},
    )
    assert a != b and a.exists() and b.exists()


def test_a_window_that_truncates_even_alone_is_counted_as_failed():
    from src.grok_client import GrokTruncationError

    class _Trunc(_Grok):
        def extract_json(self, prompt, **_k):
            raise GrokTruncationError("cut")

    failures: dict = {"calls": 0}
    se = {
        "Sub-eventID": SEID,
        "Sub-event_summary": "S",
        "Sub-event_fulltext": {"a": "x"},
    }
    assert (
        wc._batch_extract_weather([se], EVENT_ID, "C", _Trunc({}), 1, failures=failures)
        == {}
    )
    assert failures["calls"] == 1


def test_a_requoted_statement_is_the_same_statement():
    m = {
        "Sub_eventID": SEID,
        "EventID": EVENT_ID,
        "original_text": "The tide  rose fast.",
    }
    assert wc._same_statement(m, SEID, "the tide rose fast", EVENT_ID)


def test_a_placeholder_only_mention_is_dropped():
    assert (
        wc._normalize_weather_mention({"sea_state_text": "<as written, or null>"})
        is None
    )
