"""Cache-replay (diagnostics): the pipeline syncs the Grok api-cache to
s3://<bucket>/diagnostics/cache/<book>/ at end of Phase 2/3 so a run is replayable offline.
scripts/replay_cache.py downloads it back. These test the consumer side (the piece that makes the
synced cache usable) + the safe-book guard shared with the sync path.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest


def _load_replay():
    spec = importlib.util.spec_from_file_location(
        "replay_cache", "scripts/replay_cache.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["replay_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_safe_book_rejects_traversal():
    rc = _load_replay()
    for bad in ("../evil", "a/b", "a\\b", "..", "", "a b"):
        with pytest.raises(SystemExit):
            rc._safe_book(bad)
    assert rc._safe_book("XChannel") == "XChannel"
    assert rc._safe_book("USA-E-XChannel_1.2") == "USA-E-XChannel_1.2"


def test_replay_downloads_cache_from_scoped_prefix(tmp_path, monkeypatch):
    rc = _load_replay()
    # Fake S3: one page listing two cache objects under the book prefix.
    prefix = "diagnostics/cache/XChannel/"
    objs = [
        {"Key": prefix + "events/a1.json"},
        {"Key": prefix + "batch_requests.jsonl"},
    ]
    downloaded = []

    class _Pag:
        def paginate(self, **kw):
            assert kw["Prefix"] == prefix  # SCOPED to the book
            yield {"Contents": objs}

    fake_s3 = mock.Mock()
    fake_s3.get_paginator.return_value = _Pag()

    def _dl(bucket, key, target):
        downloaded.append((key, target))
        Path(target).write_text("{}", encoding="utf-8")

    fake_s3.download_file.side_effect = _dl
    monkeypatch.setattr(
        rc, "boto3", mock.Mock(client=mock.Mock(return_value=fake_s3)), raising=False
    )
    # boto3 is imported inside main(); patch the module attribute path it uses
    import boto3 as _b

    monkeypatch.setattr(_b, "client", lambda *a, **k: fake_s3)

    dest = tmp_path / "cache" / "api"
    monkeypatch.setattr(
        sys,
        "argv",
        ["replay_cache.py", "--book", "XChannel", "--dest", str(dest), "--bucket", "b"],
    )
    rc.main()

    # both files landed under dest, stripped of the prefix (replayable layout)
    assert (dest / "events" / "a1.json").exists()
    assert (dest / "batch_requests.jsonl").exists()
    assert len(downloaded) == 2


def test_replay_empty_result_exits_nonzero(tmp_path, monkeypatch):
    rc = _load_replay()

    class _Pag:
        def paginate(self, **kw):
            yield {"Contents": []}

    fake_s3 = mock.Mock()
    fake_s3.get_paginator.return_value = _Pag()
    import boto3 as _b

    monkeypatch.setattr(_b, "client", lambda *a, **k: fake_s3)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay_cache.py",
            "--book",
            "Nope",
            "--dest",
            str(tmp_path / "c"),
            "--bucket",
            "b",
        ],
    )
    with pytest.raises(SystemExit):
        rc.main()
