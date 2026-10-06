"""Regression: Wikipedia image URLs carry tracking params (?utm_source=…); the Commons
License lookup must use a CLEAN filename (no query/fragment). Caught by a live run."""

from unittest.mock import patch

from src.enrichment import equipment_wikipedia as w


def test_license_filename_strips_utm_query():
    """The filename passed to _fetch_license must not contain '?utm_...'."""
    api = {
        "query": {
            "pages": {
                "1": {
                    "title": "M4 Sherman",
                    "extract": "The M4 Sherman was an American medium tank.",
                    "original": {
                        "source": "https://upload.wikimedia.org/x/M4_Sherman.jpg"
                        "?utm_source=en.wikipedia.org&utm_campaign=api&utm_content=original"
                    },
                }
            }
        }
    }

    class _Resp:
        status_code = 200

        def json(self):
            return api

    captured = {}

    def fake_fetch_license(filename):
        captured["filename"] = filename
        return "CC BY-SA 2.0"

    with (
        patch.object(w.requests, "get", return_value=_Resp()),
        patch.object(w, "_fetch_license", fake_fetch_license),
    ):
        res = w._lookup_equipment("M4 Sherman")

    assert "?" not in captured["filename"], captured["filename"]
    assert captured["filename"] == "M4_Sherman.jpg"
    assert res["license"] == "CC BY-SA 2.0"
