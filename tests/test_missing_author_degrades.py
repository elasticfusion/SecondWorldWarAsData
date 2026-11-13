"""An empty book author must not drop an entity type (XChannel-8, 2026-11-07: the chapter metadata
had no author, weather raised 'Missing required book metadata' and persisted 0 records although
the chapter describes the sea state, wind and tide throughout). The records keep author "" and
one actionable alert per book says how to fill it; an empty BOOK still raises (it is the
provenance key)."""

import json
import logging

import pytest

from src.extraction._entity_common import require_book_provenance
from src.utils.observe import reset_counted_warnings


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_counted_warnings()
    yield
    reset_counted_warnings()


def test_empty_author_alerts_once_per_book_and_continues(caplog):
    with caplog.at_level(logging.WARNING):
        require_book_provenance("USA-E-XChannel-8", "", "weather", "ch1")
        require_book_provenance("USA-E-XChannel-8", "", "dates", "ch1")
    alerts = [
        r.getMessage() for r in caplog.records if "author_missing" in r.getMessage()
    ]
    assert len(alerts) == 1  # one per book, not per entity / event
    assert (
        "re-run Phase 1" in alerts[0].replace("_", " ")
        and "USA-E-XChannel-8" in alerts[0]
    )


def test_empty_book_still_raises():
    with pytest.raises(ValueError, match="book"):
        require_book_provenance("", "Harrison", "weather", "ch1")


def test_complete_metadata_is_silent(caplog):
    with caplog.at_level(logging.WARNING):
        require_book_provenance(
            "USA-E-XChannel-8", "Gordon A. Harrison", "weather", "ch1"
        )
    assert not caplog.records


SEID = "01M6W8ZCHP9A3NYBQSXBHDN9G3"


class _Grok:
    batch_mode = False

    def extract_json(self, prompt, **_k):
        return {
            "P001-P001": [  # the chapter's one paragraph window
                {
                    "place_name": "Omaha Beach",
                    "date": "1944-06-06",
                    "weather_description": "heavy seas, wind 15-20 knots",
                    "original_text": "The wind was 15-20 knots and the seas were heavy.",
                }
            ]
        }


def test_weather_is_extracted_with_an_empty_author(tmp_path, monkeypatch):
    from src.extraction import weather_central as wc

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    content = tmp_path / "content" / "USA-E-XChannel-8"
    content.mkdir(parents=True)
    event_file = content / "chapter1full-event.json"
    event_file.write_text(
        json.dumps(
            {
                "Chapter": "The Assault",
                "Event": {
                    "EventID": "01M6W8ZCHP9A3NYBQSXBHDN9G4",
                    "Sub-events": [
                        {
                            "Sub-eventID": SEID,
                            "Sub-event_summary": "Landing",
                            "Sub-event_fulltext": {
                                "Paragraph_001": "The seas were heavy."
                            },
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    parsed = content / "chapter1full-parsed.json"
    parsed.write_text(
        json.dumps(
            {
                "book": "USA-E-XChannel-8",
                "author": "",
                "paragraphs": [{"absolute_number": 1, "text": "The seas were heavy."}],
            }
        ),
        encoding="utf-8",
    )
    wdir = tmp_path / "weather"
    wdir.mkdir()
    wc.extract_weather_central(
        event_file, wdir, _Grok(), tmp_path / "places", parsed_file=parsed
    )
    records = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in wdir.glob("*.json")
        if f.name not in ("index.json", ".processed_events.json")
    ]
    assert records, "weather must be persisted despite the empty author"
    assert all(m["author"] == "" for r in records for m in r["event_mentions"])
    assert any("heavy seas" in json.dumps(r) for r in records)
