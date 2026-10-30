"""Phase-3 must download the book's content/ tree (events + source_section) so the event_provenance
stamp can resolve SourceSectionID — else stamp resolved=0 reason=no_source_sections and data can't be
traced to its source paragraph (provenance is the #1 project goal). XChannel-8 2026-10-30.
"""

import os
from unittest import mock

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
import ecs_entrypoint as EE


def test_provenance_inputs_downloads_content_prefix():
    calls = []
    with mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 3):
        EE._download_phase3_provenance_inputs(object(), "USA-E-XChannel-8")
    assert calls == ["output/content/USA-E-XChannel-8/"]


def test_provenance_inputs_rejects_traversal_book():
    calls = []
    with mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 0):
        EE._download_phase3_provenance_inputs(object(), "../evil")
        EE._download_phase3_provenance_inputs(object(), "")
    assert calls == []  # malicious/empty -> no download
