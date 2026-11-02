"""Step 5a: Phase-3 persist_map_entities — URL-keyed Map entity persistence with cross-chapter
reference-footprint accumulation (roman-numeral foldouts shared across chapters/books).
"""

import json
from pathlib import Path

from src.extraction.map_link_source import persist_map_entities

URL = "https://ibiblio.org/hyperwar/USA/USA-E-XChannel/maps/USA-E-XChannel-VIII.jpg"


def _map_files(d):
    return [f for f in Path(d).glob("*.json") if f.name != "index.json"]


def test_cross_chapter_accumulation(tmp_path):
    docs = [
        {
            "book": "XChannel",
            "maps": [
                {
                    "url": URL,
                    "map_id": "Map VIII",
                    "description": "Map VIII",
                    "references": [
                        {"paragraph_number": 39, "Sub_eventID": "S1"},
                        {"paragraph_number": 47},
                    ],
                }
            ],
        },
        {
            "book": "Breakout",
            "maps": [
                {
                    "url": URL,
                    "map_id": "Map VIII",
                    "description": "Map VIII",
                    "references": [{"paragraph_number": 12, "Sub_eventID": "S9"}],
                }
            ],
        },
    ]
    r = persist_map_entities(docs, tmp_path)
    assert r == {"created": 1, "appended": 1, "skipped": 0}
    files = _map_files(tmp_path)
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert len(rec["references"]) == 3
    assert {x["book"] for x in rec["references"]} == {"XChannel", "Breakout"}


def test_index_persists_across_runs(tmp_path):
    doc = {
        "book": "XChannel",
        "maps": [
            {"url": URL, "map_id": "Map VIII", "references": [{"paragraph_number": 1}]}
        ],
    }
    persist_map_entities([doc], tmp_path)  # run 1: create
    assert (tmp_path / "index.json").exists()
    doc2 = {
        "book": "XChannel",
        "maps": [
            {"url": URL, "map_id": "Map VIII", "references": [{"paragraph_number": 2}]}
        ],
    }
    r = persist_map_entities(
        [doc2], tmp_path
    )  # run 2: should APPEND (index loaded from disk)
    assert r["appended"] == 1 and r["created"] == 0
    assert len(_map_files(tmp_path)) == 1


def test_loop_guard_skips_map_source_books(tmp_path):
    docs = [
        {
            "book": "map-XChannel-VIII",
            "maps": [{"url": URL, "references": [{"paragraph_number": 1}]}],
        }
    ]
    r = persist_map_entities(docs, tmp_path)
    assert r == {"created": 0, "appended": 0, "skipped": 0}
    assert _map_files(tmp_path) == []


def test_empty_url_skipped(tmp_path):
    docs = [
        {"book": "X", "maps": [{"url": "", "references": [{"paragraph_number": 1}]}]}
    ]
    assert persist_map_entities(docs, tmp_path)["skipped"] == 1
