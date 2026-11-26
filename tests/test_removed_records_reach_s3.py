"""Task 1.5: a record the phase removes locally (a dedup merge, a narrative note moved out of
bibliography/, a shard folded in) is deleted from S3 at the final sync - the phase runs in a
subprocess, so its unlinks never reached track_deletion and the old copy came back on every
download. A record another task rewrote in S3 after this one synced it is kept and alerted.
"""

import datetime as dt
import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    monkeypatch.setattr(ee, "_downloaded_keys", {})
    monkeypatch.setattr(ee, "_synced_at", {})
    monkeypatch.setattr(ee, "_synced_etag", {})
    monkeypatch.setattr(ee, "_deleted_keys", set())
    s3 = mock.Mock()
    s3.head_object.return_value = {
        "LastModified": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    }
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append((e, k)))
    return tmp_path, s3, sent


def _download(root, key, body="{}", etag=""):
    f = root / key
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    ee._note_download(key, f, etag)
    return f


def test_a_record_the_phase_removed_is_deleted_from_s3(work):
    root, s3, sent = work
    gone = _download(root, "output/bibliography/bib_abc.json")
    _download(root, "output/bibliography/bib_kept.json")
    gone.unlink()  # e.g. relocated to narrative_notes/ by bibliography_resolve
    ee._delete_merged_from_s3()
    s3.delete_object.assert_called_once_with(
        Bucket=ee.BUCKET, Key="output/bibliography/bib_abc.json"
    )
    assert sent == []


def test_a_record_background_uploaded_then_merged_away_is_deleted(work, monkeypatch):
    root, s3, sent = work
    d = root / "output" / "people"
    d.mkdir(parents=True)
    f = d / "new_person.json"
    f.write_text("{}")
    os.utime(f, (1, 1))
    monkeypatch.setattr(ee, "_heartbeat_nat_lease", lambda: None)
    sync = ee.BackgroundSync.__new__(ee.BackgroundSync)
    sync._uploaded_mtimes = {}
    sync._sync_changed(root / "output", "output")
    f.unlink()  # merged into another person later in the run
    ee._delete_merged_from_s3()
    s3.delete_object.assert_called_once_with(
        Bucket=ee.BUCKET, Key="output/people/new_person.json"
    )


def test_a_record_another_task_rewrote_is_kept_and_alerted(work):
    root, s3, sent = work
    gone = _download(root, "output/places/p1.json")
    gone.unlink()
    s3.head_object.return_value = {
        "LastModified": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    }
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()
    [(event, fields)] = sent
    assert (
        event == "merged_delete_failed" and fields["error"] == "changed_since_download"
    )


def test_an_unknown_s3_state_never_deletes(work):
    root, s3, sent = work
    gone = _download(root, "output/places/p1.json")
    gone.unlink()
    s3.head_object.side_effect = RuntimeError("throttled")
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()
    assert sent and sent[0][0] == "merged_delete_failed"


@pytest.mark.parametrize(
    "key",
    [
        "output/content/Utah/chapter1/ch1-parsed.json",  # not an entity dir
        "output/places/index.json",  # a sidecar, not a record
        "output/images/files/map1.jpg",  # a binary
        "contentrepository/Utah/chapter1/ch1.md",  # not output/
    ],
)
def test_only_removed_entity_records_are_deleted(work, key):
    root, s3, sent = work
    _download(root, key).unlink()
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()


def test_records_still_on_disk_are_never_deleted(work):
    root, s3, sent = work
    _download(root, "output/people/p.json")
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()
    s3.head_object.assert_not_called()


def test_an_in_process_tracked_deletion_is_still_deleted(work):
    root, s3, sent = work
    f = root / "output" / "people" / "x.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    ee.track_deletion(f)
    f.unlink()
    ee._delete_merged_from_s3()
    s3.delete_object.assert_called_once_with(
        Bucket=ee.BUCKET, Key="output/people/x.json"
    )


def test_a_record_with_the_same_etag_is_deleted_and_a_new_etag_kept(work):
    root, s3, sent = work
    _download(root, "output/places/same.json", etag='"a1"').unlink()
    _download(root, "output/places/newer.json", etag='"b1"').unlink()
    s3.head_object.side_effect = lambda Bucket, Key: {
        "ETag": '"a1"' if Key.endswith("same.json") else '"b2"',
        "LastModified": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    }
    ee._delete_merged_from_s3()
    s3.delete_object.assert_called_once_with(
        Bucket=ee.BUCKET, Key="output/places/same.json"
    )
    assert sent[0][1]["error"] == "changed_since_download"


def test_a_record_removed_after_the_snapshot_keeps_its_s3_copy(work):
    """SIGTERM sync while the phase still runs: it removes a record after the snapshot, whose
    replacement was not uploaded yet - its S3 copy must survive."""
    root, s3, sent = work
    late = _download(root, "output/bibliography/bib_x.json")
    removed = (
        ee._removed_downloads()
    )  # snapshot before the uploads: nothing removed yet
    late.unlink()  # the phase moves it to narrative_notes/ during the uploads
    ee._delete_merged_from_s3(removed)
    s3.delete_object.assert_not_called()


def test_a_mass_removal_deletes_nothing_and_alerts(work):
    root, s3, sent = work
    for n in range(ee.MASS_DELETE_MIN + 1):
        _download(root, f"output/people/p{n}.json").unlink()
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()
    [(event, fields)] = sent
    assert event == "mass_delete_refused" and fields["count"] == ee.MASS_DELETE_MIN + 1


def test_a_deleted_record_is_not_looked_at_again(work):
    root, s3, sent = work
    _download(root, "output/people/p.json").unlink()
    ee._delete_merged_from_s3()
    ee._delete_merged_from_s3()  # a second final sync in the same process
    assert s3.delete_object.call_count == 1 and s3.head_object.call_count == 1


def test_a_tracked_key_outside_the_entity_records_is_never_deleted(work):
    root, s3, sent = work
    f = root / "output" / "content" / "Utah" / "ch1-parsed.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    ee.track_deletion(f)
    ee._delete_merged_from_s3()
    s3.delete_object.assert_not_called()


def test_a_record_this_task_rewrote_then_removed_is_deleted(work, monkeypatch):
    """download (ETag a1) -> the phase edits it -> background upload (new ETag) -> merged away:
    our own upload, not another task's, so it is deleted and nothing is alerted."""
    root, s3, sent = work
    f = _download(root, "output/people/p.json", etag='"a1"')
    f.write_text('{"edited": true}')
    os.utime(f, (2, 2))
    sync = ee.BackgroundSync.__new__(ee.BackgroundSync)
    sync._uploaded_mtimes = {}
    sync._sync_changed(root / "output", "output")
    f.unlink()
    s3.head_object.return_value = {
        "ETag": '"b2"',  # what our upload produced
        "LastModified": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    }
    ee._delete_merged_from_s3()
    s3.delete_object.assert_called_once_with(
        Bucket=ee.BUCKET, Key="output/people/p.json"
    )
    assert sent == []


def test_tracked_merges_are_not_refused_by_the_mass_cap(work):
    root, s3, sent = work
    for n in range(ee.MASS_DELETE_MIN + 5):
        f = root / "output" / "people" / f"m{n}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
        ee.track_deletion(f)
        f.unlink()
    ee._delete_merged_from_s3()
    assert s3.delete_object.call_count == ee.MASS_DELETE_MIN + 5
