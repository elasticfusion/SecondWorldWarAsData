"""Guard: a cold-start 404 on a FIXED optional key is not a status=error alarm, but a 404 on a
listed/manifested key still is.

XChannel ch8 e2e (clean slate) raised 15 false ``OBSERV download.file_missing status=error``
("key listed but 404") for index.json / .processed_events.json / not_duplicates.json /
run_scope — none were listed; they simply did not exist yet.
"""

import inspect
import os

import pytest
from botocore.exceptions import ClientError

# ecs_entrypoint reads S3_BUCKET at import time.
os.environ.setdefault("S3_BUCKET", "test-bucket")
import ecs_entrypoint  # noqa: E402


class _S3_404:
    def download_file(self, bucket, key, dest):
        raise ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )


@pytest.fixture
def _spy(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    calls = []
    monkeypatch.setattr(
        ecs_entrypoint, "observe_error", lambda *a, **k: calls.append((a, k))
    )
    return calls


@pytest.fixture
def _obs(monkeypatch):
    seen = []
    monkeypatch.setattr(
        ecs_entrypoint, "observe", lambda *a, **k: seen.append((a, k)) or "ok"
    )
    return seen


def test_optional_absence_is_countable_ok(_spy, _obs):
    ecs_entrypoint._download_s3_file(
        _S3_404(), "output/people/index.json", optional=True
    )
    assert _spy == []
    assert (
        _obs[0][0] == ("download", "optional_absent") and _obs[0][1]["status"] == "ok"
    )


def test_run_scope_absence_is_warn(_spy, _obs):
    ecs_entrypoint._download_s3_file(
        _S3_404(), "output/manifests/run_scope/B.json", optional="warn"
    )
    assert _spy == [] and _obs[0][1]["status"] == "warn"
    src = inspect.getsource(ecs_entrypoint._download_phase2_inputs)
    assert 'optional="warn"' in src


def test_phase1_final_sync_uploads_run_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    (tmp_path / "output" / "content" / "B").mkdir(parents=True)
    rs = tmp_path / "output" / "manifests" / "run_scope"
    rs.mkdir(parents=True)
    (rs / "B.json").write_text('{"section_count": 2}', encoding="utf-8")
    prefixes = []
    monkeypatch.setattr(
        ecs_entrypoint,
        "s3_sync_up",
        lambda d, prefix, **k: prefixes.append(prefix) or (1, []),
    )
    ecs_entrypoint._final_sync("phase1_parse.py")
    assert "output/manifests/run_scope" in prefixes


def test_optional_key_404_is_not_an_error(_spy, _obs):
    ecs_entrypoint._download_s3_file(
        _S3_404(), "output/people/not_duplicates.json", optional=True
    )
    assert _spy == []


def test_listed_key_404_is_still_an_error(_spy):
    ecs_entrypoint._download_s3_file(_S3_404(), "output/content/B/ch1-parsed.json")
    assert _spy and _spy[0][0] == ("download", "file_missing")


def test_non_404_still_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)

    class _S3_403:
        def download_file(self, *a):
            raise ClientError({"Error": {"Code": "403", "Message": "x"}}, "GetObject")

    with pytest.raises(ClientError):
        ecs_entrypoint._download_s3_file(
            _S3_403(), "output/x/index.json", optional=True
        )


def test_exclusion_migration_fetches_are_optional():
    src = inspect.getsource(ecs_entrypoint._migrate_exclusions_to_dynamo)
    assert "_download_s3_file(s3_migrate, key, optional=True)" in src


def test_processed_events_marker_fetch_is_optional():
    src = inspect.getsource(ecs_entrypoint)
    line = next(
        ln
        for ln in src.splitlines()
        if ".processed_events.json" in ln and "_download_s3_file" in ln
    )
    assert "optional=True" in line
