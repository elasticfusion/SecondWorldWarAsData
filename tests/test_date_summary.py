"""LLM date significance summary: source-grounded, provenance-stamped, staleness-gated,
fail-open; always stamps mention_count."""

import jsonschema
from src.extraction.date_summary import (
    generate_date_summary,
    needs_summary,
    _MIN_MENTION_GROWTH,
)
from src.schemas.dates_output import DATES_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


class FakeGrok:
    def __init__(self, summary="Allied forces advanced near Malmedy."):
        self.calls = 0
        self._s = summary

    def extract_json(self, *a, **k):
        self.calls += 1
        return {"summary": self._s}


def _date(n_mentions, **extra):
    d = {
        "DateID": UL,
        "date_start": "1945-01-05",
        "event_mentions": [
            {"MentionID": UL, "Event_Name": f"E{i}", "original_text": f"text {i}"}
            for i in range(n_mentions)
        ],
    }
    d.update(extra)
    return d


def test_zero_mentions_skipped():
    d = _date(0)
    g = FakeGrok()
    generate_date_summary(d, g)
    assert g.calls == 0 and "summary" not in d
    assert d["mention_count"] == 0  # count still stamped


def test_generates_and_stamps_provenance():
    d = _date(5)
    g = FakeGrok()
    generate_date_summary(d, g)
    assert g.calls == 1
    assert d["summary"] == "Allied forces advanced near Malmedy."
    assert d["summary_source"] == "synthesized"
    assert d["summary_generated_at"]
    assert d["summary_mention_count"] == 5
    assert d["mention_count"] == 5
    jsonschema.validate(d, S)


def test_staleness_skip_when_not_grown():
    # content-aware: skip ONLY when the mention set is unchanged (hash matches). Build a
    # summarized date whose stamped hash matches its current mentions -> no regen.
    from src.extraction.date_summary import _mentions_hash

    d = _date(5, summary="old", summary_source="synthesized", summary_mention_count=5)
    d["summary_mentions_hash"] = _mentions_hash(d)
    assert needs_summary(d) is False
    g = FakeGrok("NEW")
    generate_date_summary(d, g)
    assert g.calls == 0 and d["summary"] == "old"


def test_regenerates_when_grown_materially():
    d = _date(
        5 + _MIN_MENTION_GROWTH,
        summary="old",
        summary_source="synthesized",
        summary_mention_count=5,
    )
    assert needs_summary(d) is True
    g = FakeGrok("NEW significance")
    generate_date_summary(d, g)
    assert g.calls == 1 and d["summary"] == "NEW significance"
    assert d["summary_mention_count"] == 5 + _MIN_MENTION_GROWTH


def test_fail_open_on_grok_error():
    class Bad:
        def extract_json(self, *a, **k):
            raise RuntimeError("down")

    d = _date(5)
    generate_date_summary(d, Bad())
    assert "summary" not in d  # no fabrication
    assert d["mention_count"] == 5  # count still stamped


def test_grounded_prompt_uses_only_mentions(monkeypatch):
    captured = {}
    import src.extraction.date_summary as mod

    def fake_render(name, **kw):
        captured.update(kw)
        return "PROMPT"

    monkeypatch.setattr(mod, "render_prompt", fake_render, raising=False)
    # render_prompt is imported inside the function; patch the loader instead
    from src.utils import prompt_loader

    monkeypatch.setattr(prompt_loader, "render_prompt", fake_render)
    d = _date(2)
    generate_date_summary(d, FakeGrok())
    assert "text 0" in captured.get(
        "mentions", ""
    )  # the date's own mentions fed the prompt


def test_summarize_dates_pass_threaded(tmp_path):
    import json
    from src.extraction.date_summary import summarize_dates

    for i in range(4):
        (tmp_path / f"d{i}.json").write_text(
            json.dumps(
                {
                    "DateID": UL,
                    "date_start": f"1945-01-0{i+1}",
                    "event_mentions": [
                        {"MentionID": UL, "Event_Name": "E", "original_text": "t"}
                    ],
                }
            )
        )
    (tmp_path / "index.json").write_text("{}")  # must be skipped
    n = summarize_dates(tmp_path, FakeGrok("S"), max_workers=4)
    assert n == 4  # 4 date files summarized, index.json skipped
    d0 = json.loads((tmp_path / "d0.json").read_text())
    assert d0["summary"] == "S" and d0["summary_source"] == "synthesized"
