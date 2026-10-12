"""Phase 2 handler scoping invariant.

The AWS Phase 2 path is S3-event-driven: an upload of
``output/content/{Book}/chapter*-parsed.json`` must process ONLY that section —
never the rest of the corpus and never a different book. These tests pin that
invariant so a regression (e.g. switching to an rglob over the whole bucket)
is caught.
"""

# pylint: disable=missing-function-docstring,import-error

import json
from unittest.mock import MagicMock, patch

from lambda_handlers import phase2_handler as h


def _sns_s3_event(*keys, bucket="dev-wwii-data-pipeline"):
    """Build an SNS->S3 event (the real trigger shape) for the given object keys."""
    s3_records = [
        {"s3": {"bucket": {"name": bucket}, "object": {"key": k}}} for k in keys
    ]
    return {"Records": [{"Sns": {"Message": json.dumps({"Records": s3_records})}}]}


def test_extract_s3_records_only_returns_event_keys():
    event = _sns_s3_event(
        "output/content/SiegfriedLine/chapter3a-parsed.json",
        "output/content/SiegfriedLine/chapter2a-parsed.json",
    )
    recs = h._extract_s3_records(event)
    keys = [k for _b, k in recs]
    assert keys == [
        "output/content/SiegfriedLine/chapter3a-parsed.json",
        "output/content/SiegfriedLine/chapter2a-parsed.json",
    ]


def test_extract_chapter_scopes_output_to_the_keys_book(tmp_path):
    """_extract_chapter must derive the book from the key and write back ONLY to
    that book's dir — not any other book."""
    key = "output/content/SiegfriedLine/chapter3a-parsed.json"
    storage = MagicMock()
    storage.read_json.return_value = {"book": "SiegfriedLine", "paragraphs": []}

    written = {}
    storage.write_json.side_effect = lambda dest, data: written.setdefault(dest, data)

    # Mock extract_events to produce an event file in the temp output dir.
    def fake_extract_events(parsed_file, _grok, output_dir):
        out = output_dir / "chapter3a-event.json"
        out.write_text(json.dumps({"Event": {}}), encoding="utf-8")
        return out

    with patch("src.extraction.events.extract_events", side_effect=fake_extract_events):
        h._extract_chapter(key, storage, grok_client=MagicMock(), config={})

    # Read only the triggering key, and write ONLY under SiegfriedLine.
    storage.read_json.assert_called_once_with(key)
    assert list(written) == ["output/content/SiegfriedLine/chapter3a-event.json"]
    assert all(d.startswith("output/content/SiegfriedLine/") for d in written)
    assert not any("BreakoutAndPursuit" in d for d in written)


def test_handler_processes_only_the_triggering_parsed_key():
    """A single parsed-key S3 event extracts exactly that one chapter; a
    non-parsed key in the same event is ignored; no other book is touched."""
    event = _sns_s3_event(
        "output/content/SiegfriedLine/chapter3a-parsed.json",
        "output/content/BreakoutAndPursuit/chapter1a-event.json",  # not -parsed.json
    )

    extracted = []
    with (
        patch.object(h, "_get_api_key", return_value="k"),
        patch("src.grok_client.GrokClient"),
        patch("src.utils.backends.create_storage"),
        patch("src.utils.backends.create_cache_backend"),
        patch("src.utils.config.load_config", return_value={"external_maps": {}}),
        patch("src.utils.openserp_client.get_openserp_url", return_value=""),
        patch.object(
            h,
            "_extract_chapter",
            side_effect=lambda key, *a, **k: extracted.append(key),
        ),
        patch.object(h, "_notify_phase2_complete"),
    ):
        results = h.handler(event, None)

    # Only the SiegfriedLine parsed key was extracted; the other book's
    # non-parsed key was skipped.
    assert extracted == ["output/content/SiegfriedLine/chapter3a-parsed.json"]
    assert results["processed"] == 1
    assert not any("BreakoutAndPursuit" in k for k in extracted)
