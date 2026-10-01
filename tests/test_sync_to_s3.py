"""Tests for the standalone S3 sync script (pre-stage input step)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import sync_to_s3


def test_build_sync_command_targets_contentrepository():
    cmd = sync_to_s3.build_sync_command(
        Path("/local/docs"), "my-bucket", "FMS/B-Series/B 400-499", "us-east-1", False
    )
    assert cmd[:3] == ["aws", "s3", "sync"]
    assert cmd[3] == "/local/docs"
    assert cmd[4] == "s3://my-bucket/contentrepository/FMS/B-Series/B 400-499/"
    assert "--dryrun" not in cmd


def test_build_sync_command_dry_run_flag():
    cmd = sync_to_s3.build_sync_command(Path("/x"), "b", "dest", "us-east-1", True)
    assert "--dryrun" in cmd


def test_build_sync_command_excludes_archives():
    """All compressed files are ignored — pre-stage expands them."""
    cmd = sync_to_s3.build_sync_command(Path("/x"), "b", "d", "us-east-1", False)
    for pat in ("*.zip", "*.rar", "*.7z", "*.tar", "*.gz", "*.bz2", "*.xz"):
        assert pat in cmd


def test_build_sync_command_strips_dest_slashes():
    cmd = sync_to_s3.build_sync_command(Path("/x"), "b", "/Maps/", "us-east-1", False)
    assert cmd[4] == "s3://b/contentrepository/Maps/"


def test_size_guard_blocks_large_without_force(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    with patch.object(
        sync_to_s3, "dir_size_bytes", return_value=100 * 1024**3
    ):  # 100 GB
        with patch("subprocess.run") as sub:
            rc = sync_to_s3.main(
                ["--source", str(tmp_path), "--dest", "d", "--max-gb", "50"]
            )
    assert rc == 3  # refused
    sub.assert_not_called()  # never ran the sync


def test_size_guard_allows_with_force(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    with patch.object(sync_to_s3, "dir_size_bytes", return_value=100 * 1024**3):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as sub:
            rc = sync_to_s3.main(
                ["--source", str(tmp_path), "--dest", "d", "--max-gb", "50", "--force"]
            )
    assert rc == 0
    sub.assert_called_once()


def test_small_source_syncs(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF small")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as sub:
        rc = sync_to_s3.main(
            ["--source", str(tmp_path), "--dest", "FMS/test", "--dry-run"]
        )
    assert rc == 0
    sub.assert_called_once()


def test_missing_source_errors(tmp_path):
    rc = sync_to_s3.main(["--source", str(tmp_path / "nope"), "--dest", "d"])
    assert rc == 2
