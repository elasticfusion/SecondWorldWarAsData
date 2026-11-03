"""Three-zone staged-scan disposition (download-path AV): downloaded binaries land in unscanned/
<prod-path>; a scan cycle dispositions each by PREFIX SWAP — clean -> <prod-path>, infected ->
quarantine/<prod-path>, unscannable -> stays in unscanned/. All zones mirror the prod structure
(move = prefix swap). Owner contract 2026-11-02.
"""

import pytest

import src.ingestion.av_scan as av
from src.ingestion.av_scan import scan_staged_binaries, ScanResult, Verdict


class FakeS3:
    def __init__(self):
        self.objs = {}
        self.unreachable = False

    def head_bucket(self, Bucket):
        if self.unreachable:
            raise RuntimeError("no bucket")

    def put_object(self, Bucket, Key, Body=b""):
        self.objs[Key] = Body

    def copy_object(self, Bucket, CopySource, Key):
        self.objs[Key] = self.objs[CopySource["Key"]]

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def head_object(self, Bucket, Key):
        return {"Metadata": {}}


class MockScanner:
    def __init__(self, verdicts):
        self.verdicts = verdicts  # tag substring -> Verdict

    def scan(self, bucket, keys):
        out = []
        for k in keys:
            for tag, v in self.verdicts.items():
                if tag in k:
                    out.append(
                        ScanResult(
                            k, v, signature="Eicar" if v is Verdict.INFECTED else ""
                        )
                    )
                    break
        return out


@pytest.fixture(autouse=True)
def _noop_alert(monkeypatch):
    monkeypatch.setattr(av, "reject_to_review", lambda *a, **k: "marker")
    monkeypatch.setattr(av, "freeze_submitter", lambda *a, **k: None)


def _run(verdicts):
    s3 = FakeS3()
    for tag in verdicts:
        s3.objs[f"unscanned/output/maps/files/{tag}.jpg"] = b"x"
    out = scan_staged_binaries(s3, "b", list(s3.objs), MockScanner(verdicts))
    return s3, out


def test_clean_promoted_to_prod():
    s3, out = _run({"clean": Verdict.CLEAN})
    assert "output/maps/files/clean.jpg" in s3.objs
    assert "unscanned/output/maps/files/clean.jpg" not in s3.objs
    assert out.clean_keys == ["output/maps/files/clean.jpg"]


def test_infected_to_quarantine_prefix_swap():
    s3, out = _run({"virus": Verdict.INFECTED})
    assert "quarantine/output/maps/files/virus.jpg" in s3.objs
    assert "unscanned/output/maps/files/virus.jpg" not in s3.objs
    assert "quarantine/output/maps/files/virus.jpg" in out.quarantined_keys


def test_unscannable_stays_in_unscanned():
    s3, out = _run({"err": Verdict.ERROR})
    assert (
        "unscanned/output/maps/files/err.jpg" in s3.objs
    )  # NOT promoted, NOT quarantined-moved
    assert "quarantine/output/maps/files/err.jpg" not in s3.objs


def test_fail_closed_on_unreachable_dest_no_move():
    s3 = FakeS3()
    s3.objs["unscanned/output/maps/files/clean.jpg"] = b"x"
    s3.unreachable = True  # _ensure_prefix head_bucket raises -> refuse move
    out = scan_staged_binaries(
        s3, "b", list(s3.objs), MockScanner({"clean": Verdict.CLEAN})
    )
    assert "unscanned/output/maps/files/clean.jpg" in s3.objs  # not moved (fail-closed)
    assert out.clean_keys == []


def test_zones_mirror_prod_structure():
    # a nested prod path keeps its structure under every zone (move = prefix swap)
    s3 = FakeS3()
    s3.objs["unscanned/output/maps/files/sub/x.jpg"] = b"x"
    scan_staged_binaries(
        s3, "b", list(s3.objs), MockScanner({"x.jpg": Verdict.INFECTED})
    )
    assert "quarantine/output/maps/files/sub/x.jpg" in s3.objs


def test_clean_but_promote_failed_is_loud_not_silent(monkeypatch):
    # scanner clears the file but the move to prod fails -> must NOT vanish silently: stays in
    # unscanned/, tracked in outcome (surfaces in av.summary), logged av.promote_failed.
    s3 = FakeS3()
    s3.objs["unscanned/output/maps/files/ok.jpg"] = b"x"

    def boom(**k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(s3, "copy_object", boom)
    out = scan_staged_binaries(
        s3, "b", list(s3.objs), MockScanner({"ok": Verdict.CLEAN})
    )
    assert "unscanned/output/maps/files/ok.jpg" in s3.objs  # stays
    assert "output/maps/files/ok.jpg" not in s3.objs  # not promoted
    assert out.clean_keys == []  # not counted clean
    assert (
        "unscanned/output/maps/files/ok.jpg" in out.quarantined_keys
    )  # tracked, not silent


def test_traversal_staged_key_rejected():
    s3 = FakeS3()
    s3.objs["unscanned/../output/evil.jpg"] = b"x"
    s3.objs["unscanned/output/ok.jpg"] = b"x"
    out = scan_staged_binaries(
        s3,
        "b",
        list(s3.objs),
        MockScanner({"evil": Verdict.CLEAN, "ok": Verdict.CLEAN}),
    )
    assert (
        "../output/evil.jpg" not in s3.objs and "output/evil.jpg" not in s3.objs
    )  # rejected
    assert "output/ok.jpg" in s3.objs  # legit promoted


class IdentityS3:
    """head_object returns the stamped identity ONLY while the key exists (like real S3):
    a read AFTER copy+delete returns empty metadata. Proves identity is captured pre-delete.
    """

    def __init__(self, key: str):
        self.objs = {key: b"x"}
        self.meta = {key: {"api-key-id": "key-ABC"}}

    def head_bucket(self, Bucket):
        pass

    def put_object(self, Bucket, Key, Body=b""):
        self.objs[Key] = Body

    def copy_object(self, Bucket, CopySource, Key):
        self.objs[Key] = self.objs[CopySource["Key"]]
        self.meta[Key] = self.meta.get(CopySource["Key"], {})

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)
        self.meta.pop(Key, None)

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            return {"Metadata": {}}  # deleted -> no identity (the bug would hit this)
        return {"Metadata": self.meta.get(Key, {})}


def test_infected_freeze_receives_identity_captured_before_delete(monkeypatch):
    """M1: _submitter_identity must be read BEFORE _move_key deletes the source key,
    else freeze_submitter is permanently blinded (always '')."""
    captured = {}
    monkeypatch.setattr(av, "reject_to_review", lambda *a, **k: "marker")
    monkeypatch.setattr(
        av,
        "freeze_submitter",
        lambda s3, bucket, identity, **k: captured.update(identity=identity),
    )
    key = "unscanned/output/maps/files/virus.jpg"
    s3 = IdentityS3(key)
    scan_staged_binaries(s3, "b", [key], MockScanner({"virus": Verdict.INFECTED}))
    assert (
        captured.get("identity") == "key-ABC"
    ), "freeze_submitter got a blinded identity -> _submitter_identity was read AFTER delete"
