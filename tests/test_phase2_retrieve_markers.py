"""Regression test: Phase-2 retrieve must download the optional-entity
processed-events markers.

Bug: `--retrieve-only` re-ran the full phase2_extract, and because
`output/{type}/.processed_events.json` was never downloaded, `_is_processed`
always returned False and EVERY optional entity (weather/equipment/logistics/
casualties/supplemental) was re-extracted LIVE on every retrieve — redoing most
of extraction each run (cost + ~1h latency). The fix downloads those markers so
the skip logic can fire.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee


def test_phase2_retrieve_downloads_processed_events_markers():
    """_download_phase2_inputs must request each optional-entity
    .processed_events.json marker (so _is_processed can skip them)."""
    requested_keys = []

    def fake_download(s3, key):
        requested_keys.append(key)

    with (
        patch.object(ee, "_s3_client", return_value=MagicMock()),
        patch.object(ee, "_download_s3_file", side_effect=fake_download),
        patch.object(ee, "_read_manifest", return_value=[]),
        patch.object(ee, "_list_s3_keys_matching", return_value=set()),
        patch.object(ee, "_download_new_parsed", return_value=0),
        patch.object(ee, "_read_s3_manifest", return_value=[]),
        patch.dict(os.environ, {"FORCE_DOWNLOAD_PARSED": "1", "BOOK_NAME": "X"}),
    ):
        # entity_store may be None (file mode) — tolerate either; we only care
        # about the marker downloads below.
        try:
            ee._download_phase2_inputs()
        except Exception:
            pass

    for etype in ("weather", "equipment", "logistics", "casualties", "supplemental"):
        marker = f"output/{etype}/.processed_events.json"
        assert marker in requested_keys, f"missing marker download: {marker}"
