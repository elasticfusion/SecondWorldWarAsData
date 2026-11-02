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
