"""Regression tests for the PR-E PATTERN-8 observability fixes (OBSERVABILITY_PATTERN8_AUDIT.md).

Every failure/drop/degraded path below must now emit a LOUD canonical ``OBSERV <scope>.<event>
status=error`` at the point of loss (and, for the behaviour-changing findings, STOP the data loss):

  #9  dedup/exclusions.py  _load_local / _load_name_local / _load_dynamo / _load_name_dynamo
      -> a read FAILURE emits ``OBSERV dedup.exclusions_load_error status=error`` and RAISES
         (fail CLOSED) instead of silently returning an empty set. A legitimately absent store
         still returns empty with no error.
  #34 dedup/exclusions.py  add_by_name / _add_local
      -> a read failure on an EXISTING curated file emits ``OBSERV dedup.exclusions_reset
         status=error`` and REFUSES to overwrite the curated file.
  #10 dedup/merge.py  merge_generic primary-load except
      -> emits ``OBSERV dedup.merge_load_error status=error`` before returning None.
  #30 dedup/merge.py  _backup_before_delete
      -> on backup failure emits ``OBSERV dedup.backup_failed status=error`` and the caller does
         NOT delete the secondary (undo-impossible).
  #31 dedup/merge.py  _rebase_event_file / update_event_refs
      -> an unreadable event/entity file emits ``OBSERV dedup.ref_rebase_error status=error``
         instead of a silent skip.
  #15 ecs_entrypoint.py  _download_s3_file 404 skip
      -> emits ``OBSERV download.file_missing status=error key=..`` (a parsed file vanishing
         between list and download is loud, not debug).

observe() logs to the logger named "observe"; caplog is captured at ERROR. NOTIFICATION_TOPIC_ARN
is deleted so the status=error Slack escalation is a no-op (offline/CI).
"""

import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

# ecs_entrypoint reads S3_BUCKET at import time — set a dummy before it is imported anywhere.
os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.dedup import merge as m
from src.dedup.exclusions import ExclusionLoadError, ExclusionStore


@pytest.fixture(autouse=True)
def _offline_observe(monkeypatch):
    """Keep observe()'s status=error Slack escalation offline for every test in this module."""
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


def _observ(caplog, scope, event):
    """All captured ``OBSERV <scope>.<event> status=error`` message lines."""
    needle = f"OBSERV {scope}.{event} status=error"
    return [r.message for r in caplog.records if needle in r.message]


# --------------------------------------------------------------------------------------------- #9
class TestExclusionsLoadFailClosed:
    """#9: a load FAILURE is loud + fail-closed (raises); a legitimate empty is silent + empty."""

    def test_local_corrupt_file_raises_and_observes(self, tmp_path, caplog):
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        (entity_dir / "not_duplicates.json").write_text("{ this is not json")
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            with pytest.raises(ExclusionLoadError):
                store.load()
        assert _observ(caplog, "dedup", "exclusions_load_error")

    def test_local_name_corrupt_file_raises_and_observes(self, tmp_path, caplog):
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        (entity_dir / ".name_exclusions.json").write_text("<<not json>>")
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            with pytest.raises(ExclusionLoadError):
                store.load_name_exclusions()
        assert _observ(caplog, "dedup", "exclusions_load_error")

    def test_dynamo_scan_error_raises_and_observes(self, caplog):
        table = MagicMock()
        table.scan.side_effect = RuntimeError("dynamo unavailable")
        store = ExclusionStore("groups", dynamo_table=table)
        with caplog.at_level(logging.ERROR, logger="observe"):
            with pytest.raises(ExclusionLoadError):
                store.load()
        assert _observ(caplog, "dedup", "exclusions_load_error")

    def test_dynamo_name_scan_error_raises_and_observes(self, caplog):
        table = MagicMock()
        table.scan.side_effect = RuntimeError("dynamo unavailable")
        store = ExclusionStore("groups", dynamo_table=table)
        with caplog.at_level(logging.ERROR, logger="observe"):
            with pytest.raises(ExclusionLoadError):
                store.load_name_exclusions()
        assert _observ(caplog, "dedup", "exclusions_load_error")

    def test_absent_store_is_empty_not_error(self, tmp_path, caplog):
        """A legitimately absent store is NOT a failure — returns empty, emits no error."""
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            assert store.load() == set()
            assert store.load_name_exclusions() == set()
        assert not _observ(caplog, "dedup", "exclusions_load_error")


# -------------------------------------------------------------------------------------------- #34
class TestExclusionsNoOverwriteOnReadFail:
    """#34: a read failure on an EXISTING curated file is loud AND must not overwrite it."""

    def test_add_local_read_fail_preserves_file(self, tmp_path, caplog):
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        curated = entity_dir / "not_duplicates.json"
        original = "{ corrupted but PRECIOUS curated bytes"
        curated.write_text(original)
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            store.add("a.json", "b.json")  # routes to _add_local
        assert (
            curated.read_text() == original
        ), "curated file was overwritten after read fail"
        assert _observ(caplog, "dedup", "exclusions_reset")

    def test_add_by_name_read_fail_preserves_file(self, tmp_path, caplog):
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        curated = entity_dir / ".name_exclusions.json"
        original = "<<corrupt but precious>>"
        curated.write_text(original)
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            store.add_by_name("Alice", "Bob")
        assert (
            curated.read_text() == original
        ), "curated name file overwritten after read fail"
        assert _observ(caplog, "dedup", "exclusions_reset")

    def test_add_local_creates_when_absent(self, tmp_path, caplog):
        """An absent store is legitimately created — not treated as a read failure."""
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        store = ExclusionStore("people", entity_dir=entity_dir)
        with caplog.at_level(logging.ERROR, logger="observe"):
            store.add("a.json", "b.json")
        data = json.loads((entity_dir / "not_duplicates.json").read_text())
        assert data["exclusions"]
        assert not _observ(caplog, "dedup", "exclusions_reset")


# -------------------------------------------------------------------------------------------- #10
class TestMergeLoadError:
    """#10: an unreadable merge_generic primary is a loud OBSERV before the None no-op."""

    def test_unreadable_primary_observes_and_returns_none(self, tmp_path, caplog):
        entity_dir = tmp_path / "places"
        entity_dir.mkdir()
        (entity_dir / "p1.json").write_text("{ not valid json")
        entries = [
            {"filename": "p1.json", "name": "P1"},
            {"filename": "p2.json", "name": "P2"},
        ]
        with caplog.at_level(logging.ERROR, logger="observe"):
            result = m.merge_generic(entity_dir, entries, 0, id_field="PlaceID")
        assert result is None
        assert _observ(caplog, "dedup", "merge_load_error")


# -------------------------------------------------------------------------------------------- #30
class TestBackupFailedNoDelete:
    """#30: a failed backup is loud AND the secondary is NOT deleted (undo-impossible)."""

    def test_backup_failure_observes_and_returns_false(self, tmp_path, caplog):
        entity_dir = tmp_path / "people"
        entity_dir.mkdir()
        f = entity_dir / "victim.json"
        f.write_text("{}")
        with (
            caplog.at_level(logging.ERROR, logger="observe"),
            patch.object(m.shutil, "copy2", side_effect=OSError("disk full")),
        ):
            ok = m._backup_before_delete(f)
        assert ok is False
        assert f.exists()
        assert _observ(caplog, "dedup", "backup_failed")

    def test_generic_merge_does_not_delete_on_backup_failure(self, tmp_path, caplog):
        places = tmp_path / "places"
        places.mkdir()
        (places / "p1.json").write_text(
            json.dumps({"PlaceID": "01" + "P" * 24, "name": "P1", "event_mentions": []})
        )
        (places / "p2.json").write_text(
            json.dumps({"PlaceID": "01" + "Q" * 24, "name": "P2", "event_mentions": []})
        )
        entries = [
            {"filename": "p1.json", "name": "P1"},
            {"filename": "p2.json", "name": "P2"},
        ]
        # Primary write succeeds (data is safe) but the pre-delete backup fails.
        with (
            caplog.at_level(logging.ERROR, logger="observe"),
            patch.object(m, "_write_entity_guarded", return_value=True),
            patch.object(m.shutil, "copy2", side_effect=OSError("disk full")),
        ):
            m.merge_generic(places, entries, 0, id_field="PlaceID")
        assert (places / "p2.json").exists(), "secondary deleted despite backup failure"
        assert _observ(caplog, "dedup", "backup_failed")


# -------------------------------------------------------------------------------------------- #31
class TestRefRebaseError:
    """#31: an unreadable event/entity file during rebase is a loud OBSERV, not a silent skip."""

    def test_unreadable_event_file_observes(self, tmp_path, caplog):
        f = tmp_path / "bad-event.json"
        f.write_text("{ not json")
        with caplog.at_level(logging.ERROR, logger="observe"):
            changed = m._rebase_event_file(f, "OLD", "NEW", "people")
        assert changed == 0
        assert _observ(caplog, "dedup", "ref_rebase_error")

    def test_unreadable_entity_file_observes(self, tmp_path, caplog):
        output_root = tmp_path / "output"
        people_dir = output_root / "people"
        people_dir.mkdir(parents=True)
        (people_dir / "corrupt.json").write_text("{ not json")
        with caplog.at_level(logging.ERROR, logger="observe"):
            m.update_event_refs(output_root, "OLD", "NEW", "people")
        assert _observ(caplog, "dedup", "ref_rebase_error")


# -------------------------------------------------------------------------------------------- #15
class TestDownloadFileMissing:
    """#15: a 404 on a listed key (parsed file vanished) is a loud OBSERV, not debug."""

    def test_404_emits_download_file_missing(self, tmp_path, caplog, monkeypatch):
        import ecs_entrypoint as ee
        from botocore.exceptions import ClientError

        monkeypatch.setattr(ee, "WORKDIR", tmp_path)
        s3 = MagicMock()
        s3.download_file.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject"
        )
        with caplog.at_level(logging.ERROR, logger="observe"):
            ee._download_s3_file(s3, "output/people/gone.json")
        assert _observ(caplog, "download", "file_missing")

    def test_non_404_still_raises(self, tmp_path, monkeypatch):
        import ecs_entrypoint as ee
        from botocore.exceptions import ClientError

        monkeypatch.setattr(ee, "WORKDIR", tmp_path)
        s3 = MagicMock()
        s3.download_file.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "boom"}}, "GetObject"
        )
        with pytest.raises(ClientError):
            ee._download_s3_file(s3, "output/people/x.json")
