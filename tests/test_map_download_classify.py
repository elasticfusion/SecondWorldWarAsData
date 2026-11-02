"""Step 5b: Phase-3 map download + classify (deterministic). classify runs always (create);
download gated + create-only (idempotent — a cross-chapter append never re-downloads/re-classifies).
"""

import json
import glob
from pathlib import Path
from unittest.mock import patch

from src.extraction.map_common import classify_map_type, download_map_image
from src.extraction.map_link_source import persist_map_entities

URL = "https://ibiblio.org/hyperwar/USA/USA-E-XChannel/maps/USA-E-XChannel-VIII.jpg"


def _rec(d):
    f = [x for x in glob.glob(str(Path(d) / "*.json")) if "index" not in x][0]
    return json.loads(Path(f).read_text())


def test_classify_map_type_vocab():
    assert classify_map_type("Utah Beach Assault") == "operational"
    assert classify_map_type("Situation 6 June") == "situation"
    assert classify_map_type("Map VIII") is None  # honest unknown, not guessed


def test_create_classifies_download_off(tmp_path):
    persist_map_entities(
        [
            {
                "book": "X",
                "maps": [
                    {
                        "url": URL,
                        "map_id": "Map VIII",
                        "description": "Utah Beach Assault",
                        "references": [{"paragraph_number": 1}],
                    }
                ],
            }
        ],
        tmp_path,
        download=False,
    )
    rec = _rec(tmp_path)
    assert rec["map_type"] == "operational"
    assert rec["local_image_path"] is None


def test_append_is_idempotent_no_reclassify(tmp_path):
    persist_map_entities(
        [
            {
                "book": "X",
                "maps": [
                    {
                        "url": URL,
                        "map_id": "M",
                        "description": "Utah Beach Assault",
                        "references": [{"paragraph_number": 1}],
                    }
                ],
            }
        ],
        tmp_path,
        download=False,
    )
    r = persist_map_entities(
        [
            {
                "book": "Y",
                "maps": [
                    {
                        "url": URL,
                        "map_id": "M",
                        "description": "Utah Beach Assault",
                        "references": [{"paragraph_number": 2}],
                    }
                ],
            }
        ],
        tmp_path,
        download=False,
    )
    assert r["existing"] == 1 and r["created"] == 0
    rec = _rec(tmp_path)
    assert rec["map_type"] == "operational" and len(rec["references"]) == 2


def test_download_on_sets_local_path(tmp_path):
    with patch(
        "src.extraction.images.fetch_image_bytes",
        return_value=(b"\xff\xd8\xff j", "jpg"),
    ):
        persist_map_entities(
            [
                {
                    "book": "X",
                    "maps": [
                        {
                            "url": URL,
                            "map_id": "M",
                            "description": "Assault",
                            "references": [{"paragraph_number": 1}],
                        }
                    ],
                }
            ],
            tmp_path,
            download=True,
        )
    rec = _rec(tmp_path)
    assert rec["local_image_path"] and rec["file_format"] == "jpg"
    assert len(glob.glob(str(tmp_path / "files" / "*"))) == 1


def test_download_blocked_is_graceful(tmp_path):
    with patch("src.extraction.images.fetch_image_bytes", return_value=(None, None)):
        persist_map_entities(
            [
                {
                    "book": "X",
                    "maps": [
                        {
                            "url": URL,
                            "map_id": "M",
                            "description": "Assault",
                            "references": [{"paragraph_number": 1}],
                        }
                    ],
                }
            ],
            tmp_path,
            download=True,
        )
    rec = _rec(tmp_path)
    assert rec["local_image_path"] is None  # blocked download -> no path, no crash
