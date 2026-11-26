"""ClamAV on by default for every binary (owner 2026-11-23): whatever a task uploads - the final
sync, the background sync every two minutes, or a direct storage write (portraits, award files,
photos) - a binary lands in unscanned/ and reaches its own key only after a clean scan.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.ingestion.av_scan import UNSCANNED_PREFIX, _prod_path, staged_key  # noqa: E402


@pytest.mark.parametrize(
    "key",
    [
        "output/images/files/01H.jpg",
        "output/maps/files/m.PNG",
        "artifacts/patch/01H/original/patch.webp",
        "person_photos/John_Doe/abc.jpeg",
        "output/award_pages/x.pdf",
    ],
)
def test_a_binary_goes_to_the_staging_area(key):
    assert staged_key(key) == UNSCANNED_PREFIX + key
    assert _prod_path(staged_key(key)) == key  # a clean scan promotes it to its own key


@pytest.mark.parametrize(
    "key",
    [
        "output/people/p.json",
        "find-pages/pages/a.html",
        "unscanned/output/maps/files/m.jpg",
        "diagnostics/cache/_all/cache.db",  # the pipeline's own state, never served
    ],
)
def test_text_staged_and_internal_keys_are_unchanged(key):
    assert staged_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "person_photos/x/a.svg",
        "person_photos/x/a.avif",
        "person_photos/x/a.x-icon",
        "output/photos/files/a.img",
        "artifacts/doc/01H/original/README",  # no suffix at all
        "person_photos/x/a.jpg.bin",
    ],
)
def test_anything_not_known_text_is_staged(key):
    assert staged_key(key) == UNSCANNED_PREFIX + key  # deny by default


def test_bytes_that_are_not_text_are_staged_whatever_the_suffix():
    # a hostile server's bytes saved under a Content-Type-derived .html (award_source_pages)
    for body in (b"PK\x03\x04zip", b"MZ\x90\x00exe", "Ä".encode("latin-1")):
        assert staged_key("person_photos/x/a.html", body).startswith(UNSCANNED_PREFIX)
    assert (
        staged_key("award_pages/x.html", "<p>Écouché</p>".encode())
        == "award_pages/x.html"
    )


def test_a_binary_after_a_long_text_lead_in_is_staged():
    body = (
        b"a" * (1024 * 1024 + 10) + b"\x00\x01PK"
    )  # the review's bypass of a 1 MB sniff
    assert staged_key("award_pages/x.html", body).startswith(UNSCANNED_PREFIX)


@pytest.mark.parametrize(
    "magic", [b"%PDF-1.7 ascii pdf", b"#!/bin/sh\necho", b"GIF89a"]
)
def test_a_text_looking_body_with_a_binary_signature_is_staged(magic):
    assert staged_key("award_pages/x.html", magic).startswith(UNSCANNED_PREFIX)


def test_only_the_cache_copy_is_exempt():
    assert (
        staged_key("diagnostics/cache/_all/cache.db")
        == "diagnostics/cache/_all/cache.db"
    )
    assert staged_key("diagnostics/other/x.png").startswith(UNSCANNED_PREFIX)


def _fake_s3(monkeypatch, e):
    up = []
    monkeypatch.setattr(
        e,
        "_s3_client",
        lambda: type("S", (), {"upload_file": lambda s, p, b, k: up.append(k)})(),
    )
    return up


def test_the_final_sync_stages_binaries_and_keeps_them_out_of_the_manifest(
    monkeypatch, tmp_path
):
    import ecs_entrypoint as e

    up = _fake_s3(monkeypatch, e)
    d = tmp_path / "images"
    (d / "files").mkdir(parents=True)
    (d / "01H.json").write_text("{}")
    (d / "files" / "01H.jpg").write_bytes(b"\xff\xd8")
    n, keys = e.s3_sync_up(d, "output/images")
    assert n == 2
    assert sorted(up) == [
        "output/images/01H.json",
        "unscanned/output/images/files/01H.jpg",
    ]
    assert keys == [
        "output/images/01H.json"
    ]  # the next phase never fetches an unscanned file


def test_the_background_sync_stages_binaries_too(monkeypatch, tmp_path):
    import time

    import ecs_entrypoint as e

    up = _fake_s3(monkeypatch, e)
    art = tmp_path / "artifacts" / "patch" / "01H" / "original"
    art.mkdir(parents=True)
    (art / "patch.png").write_bytes(b"\x89PNG")
    (tmp_path / "artifacts" / "patch" / "01H" / "manifest.json").write_text("{}")
    old = time.time() - 10  # not mid-write
    for f in (tmp_path / "artifacts").rglob("*"):
        if f.is_file():
            os.utime(f, (old, old))
    sync = e.BackgroundSync.__new__(e.BackgroundSync)
    sync._uploaded_mtimes = {}
    sync._sync_changed(tmp_path / "artifacts", "artifacts")
    assert sorted(up) == [
        "artifacts/patch/01H/manifest.json",
        "unscanned/artifacts/patch/01H/original/patch.png",
    ]


class _S3:
    class exceptions:
        ClientError = KeyError

    def __init__(self, present=()):
        self.present, self.puts = set(present), []

    def put_object(self, Bucket, Key, Body):
        self.puts.append(Key)

    def head_object(self, Bucket, Key):
        if Key not in self.present:
            raise KeyError(Key)


def _storage(monkeypatch, s3, prefix=""):
    from src.utils.storage import S3Storage

    monkeypatch.setattr("boto3.client", lambda *a, **k: s3)
    return S3Storage(bucket="b", prefix=prefix)


def test_a_direct_storage_write_of_a_binary_is_staged(monkeypatch):
    s3 = _S3()
    store = _storage(monkeypatch, s3)
    store.write_bytes("person_photos/John_Doe/abc.jpg", b"\xff\xd8")
    store.write_bytes("award_pages/x.html", b"<p>x</p>")
    store.write_bytes("person_photos/John_Doe/def.html", b"\x00\x01binary")
    assert s3.puts == [
        "unscanned/person_photos/John_Doe/abc.jpg",
        "award_pages/x.html",
        "unscanned/person_photos/John_Doe/def.html",
    ]


def test_a_binary_waiting_for_its_scan_counts_as_written(monkeypatch):
    s3 = _S3(present={"unscanned/output/photos/files/a.png"})
    store = _storage(monkeypatch, s3, prefix="output")
    assert store.exists("photos/files/a.png")  # not written (and staged) again
    assert not store.exists("photos/files/b.png")


def test_the_deploy_never_goes_out_with_av_off():
    text = Path("scripts/deploy_all.sh").read_text()
    assert "AV scanning stays disabled" not in text
    assert "NOT deploying with AV scanning off" in text


# --- the drain runs in its own invocations, bounded by the Lambda's time ----------------------


def _drain_world(monkeypatch, keys, verdict="clean"):
    from src.ingestion import av_scan

    scanned = []
    monkeypatch.setattr(av_scan, "_acquire_drain_lock", lambda c, b: True)
    monkeypatch.setattr(av_scan, "_release_drain_lock", lambda c, b: None)
    monkeypatch.setattr(av_scan, "_list_unscanned_keys", lambda c, b: list(keys))

    def scan_chunk(client, bucket, chunk, scanner, region=""):
        scanned.append(list(chunk))
        if verdict == "clean":
            return av_scan.AvOutcome(clean_keys=list(chunk))
        return av_scan.AvOutcome()  # no verdict for any key (ClamAV down)

    monkeypatch.setattr(av_scan, "scan_staged_binaries", scan_chunk)
    return av_scan, scanned


KEYS = [f"unscanned/output/images/files/{i}.jpg" for i in range(60)]


def test_a_drain_past_its_deadline_still_scans_one_chunk(monkeypatch):
    import time

    av_scan, scanned = _drain_world(monkeypatch, KEYS)
    out = av_scan.drain_unscanned(
        object(), "b", object(), chunk_size=25, deadline=time.monotonic() - 1
    )
    assert len(scanned) == 1 and out.more is True  # progress, then the next invocation


def test_a_drain_with_time_scans_everything(monkeypatch):
    import time

    av_scan, scanned = _drain_world(monkeypatch, KEYS)
    out = av_scan.drain_unscanned(
        object(), "b", object(), chunk_size=25, deadline=time.monotonic() + 60
    )
    assert sorted(k for c in scanned for k in c) == sorted(KEYS) and out.more is False


def test_no_verdict_from_clamav_stops_the_drain(monkeypatch, caplog):
    import logging
    import time

    av_scan, scanned = _drain_world(monkeypatch, KEYS, verdict="none")
    with caplog.at_level(logging.ERROR):
        out = av_scan.drain_unscanned(
            object(), "b", object(), chunk_size=25, deadline=time.monotonic() + 60
        )
    assert len(scanned) == 1 and out.more is False
    assert "OBSERV av.drain_no_verdict" in caplog.text


def test_each_drain_starts_somewhere_new(monkeypatch):
    import random

    av_scan, scanned = _drain_world(monkeypatch, KEYS)
    firsts = set()
    for seed in range(5):
        random.seed(seed)
        scanned.clear()
        av_scan.drain_unscanned(object(), "b", object(), chunk_size=25)
        firsts.add(scanned[0][0])
    assert len(firsts) > 1  # a stuck first chunk cannot starve the keys behind it


def test_a_drain_that_made_no_progress_does_not_reinvoke(monkeypatch):
    import lambda_handlers.trigger_handler as th
    import src.ingestion.av_scan as av

    started = []
    monkeypatch.setattr(
        av, "drain_unscanned", lambda *a, **k: av.AvOutcome(more=True)
    )  # deadline reached, nothing decided
    monkeypatch.setattr(th, "_make_av_scanner", lambda: object())
    monkeypatch.setattr(th, "_start_av_drain", lambda reason: started.append(reason))
    th._run_av_drain(None)
    assert started == []


def test_a_drain_with_keys_left_hands_them_to_a_new_invocation(monkeypatch):
    import time

    import lambda_handlers.trigger_handler as th
    import src.ingestion.av_scan as av

    seen = {}

    def drain(*a, deadline=None, **k):
        seen["deadline"] = deadline
        return av.AvOutcome(more=True, clean_keys=["unscanned/x.jpg"])

    started = []
    monkeypatch.setattr(av, "drain_unscanned", drain)
    monkeypatch.setattr(th, "_make_av_scanner", lambda: object())
    monkeypatch.setattr(th, "_start_av_drain", lambda reason: started.append(reason))

    class _Ctx:
        def get_remaining_time_in_millis(self):
            return 900_000

    assert th._run_av_drain(_Ctx())["more"] is True
    assert started == ["continue"]
    # a chunk is started only while a whole one (launch + 600 s poll) still fits
    assert seen["deadline"] - time.monotonic() <= 900 - th._AV_CHUNK_BUDGET_S


def test_a_drain_is_requested_asynchronously(monkeypatch):
    import lambda_handlers.trigger_handler as th

    calls = []

    class _Lambda:
        def invoke(self, **k):
            calls.append(k)

    monkeypatch.setattr(th, "AV_SCAN_ENABLED", True)
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: _Lambda())
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "dev-wwii-trigger")
    th._start_av_drain("phase-2")
    (call,) = calls
    assert (
        call["InvocationType"] == "Event" and call["FunctionName"] == "dev-wwii-trigger"
    )
    assert b'"av-drain"' in call["Payload"]


def test_the_schedule_and_the_handler_route_drains(monkeypatch):
    import lambda_handlers.trigger_handler as th

    started, ran = [], []
    monkeypatch.setattr(th, "_start_av_drain", lambda reason: started.append(reason))
    monkeypatch.setattr(
        th, "_run_av_drain", lambda ctx: ran.append(ctx) or {"ok": True}
    )
    monkeypatch.setattr(th, "_sweep_stop_reports_safely", lambda: None)
    monkeypatch.setattr(th, "_handle_scheduled_check", lambda: {})
    th.handler({"source": "scheduled"}, None)
    th.handler({"source": "av-drain"}, "ctx")
    assert started == ["scheduled"] and ran == ["ctx"]
