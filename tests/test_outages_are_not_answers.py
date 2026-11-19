"""Task 1.2f (owner 2026-11-18): every external and AWS service can fail; an outage is never
recorded as an answer ("not found online", a used-up attempt, a silent skip), the work is
retried, a lasting outage of a service the run needs stops it (the book is deferred and resumes),
and the operator is told what to check."""

import json
import os
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.utils import grok_models, service_outage  # noqa: E402


def _resp(code, body=None):
    return SimpleNamespace(status_code=code, json=lambda: body or {})


# --- the shared recorder ------------------------------------------------------------------------


def test_a_failure_is_noted_in_the_current_work_item_and_counted():
    with service_outage.watch() as seen:
        assert service_outage.failed("gutendex", "candidates", "Timeout") == 1
        assert service_outage.failed("gutendex", "candidates", "Timeout") == 2
    assert seen == ["gutendex"]
    service_outage.ok("gutendex")
    assert service_outage.failed("gutendex", "candidates", "Timeout") == 1


def test_a_lasting_outage_stops_a_phase_run(monkeypatch):
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
    for _ in range(service_outage.DYNAMO_STOP_AFTER - 1):
        service_outage.dynamodb_failed("url_claim", RuntimeError())
    with pytest.raises(grok_models.ServiceUnreachable):
        service_outage.dynamodb_failed("url_claim", RuntimeError())


def test_outside_a_phase_task_nothing_is_stopped(monkeypatch):
    monkeypatch.delenv("PIPELINE_PHASE", raising=False)
    for _ in range(service_outage.DYNAMO_STOP_AFTER + 2):
        service_outage.dynamodb_failed("url_claim", RuntimeError())


# --- search sources: an outage is not "no candidates" -------------------------------------------


@pytest.fixture
def fetch(monkeypatch):
    import src.utils.fetch_provenance as fp

    box = {}

    def fake(source, url, **k):
        if isinstance(box["r"], Exception):
            raise box["r"]
        return box["r"]

    monkeypatch.setattr(fp, "recorded_fetch", fake)
    return box


@pytest.mark.parametrize(
    "fn,args,service",
    [("search_gutenberg_candidates", ("A Long Enough Title",), "gutendex"),
     ("search_archive_org_candidates", ("A Long Enough Title",), "archive_org"),
     ("fetch_archive_org_metadata", ("ident",), "archive_org"),
     ("search_openserp", ("A Long Enough Title",), "openserp"),
     ("search_gutenberg_openserp", ("A Long Enough Title",), "openserp")],
)  # fmt: skip
@pytest.mark.parametrize(
    "outage", [_resp(503), _resp(429), _resp(403), ConnectionError("down")]
)
def test_a_search_source_outage_is_noted(fetch, fn, args, service, outage):
    from src.extraction import supplemental_search as ss

    fetch["r"] = outage
    with service_outage.watch() as seen:
        out = getattr(ss, fn)(*args)
    assert not out and seen == [service]


def test_an_answer_with_nothing_found_is_a_real_miss(fetch):
    from src.extraction import supplemental_search as ss

    fetch["r"] = _resp(200, {"results": []})
    with service_outage.watch() as seen:
        assert ss.search_gutenberg_candidates("A Long Enough Title") == []
    fetch["r"] = _resp(404)
    with service_outage.watch() as seen404:
        assert ss.fetch_archive_org_metadata("ident") is None
    assert seen == [] and seen404 == []  # a definite answer, not an outage


def test_an_outage_while_resolving_a_citation_is_not_queued_for_a_human(
    monkeypatch, tmp_path
):
    from src.enrichment import bibliography_resolver as br

    def resolve(data, *a):
        service_outage.failed("archive_org", "candidates", "Timeout")
        data["search_status"] = "not_found"
        return None

    monkeypatch.setattr(br, "_resolve_or_reuse", resolve)
    import src.utils.file_lock as fl

    monkeypatch.setattr(fl, "write_json_with_lock", lambda *a, **k: True)
    status, _deduped, _ = br._process_bib_record(tmp_path / "b.json", {}, None, {}, {})
    assert status == "backend_down"
    stats = {"backend_down": 0, "not_found": 0, "deduped": 0}
    assert br._account_bib_status(stats, status, False) is False  # not queued


def test_without_a_nara_key_an_archive_citation_is_an_outage_not_a_miss(monkeypatch):
    from src.enrichment import bibliography_resolver as br

    monkeypatch.setattr(br, "_is_nara_searchable", lambda e, v: True)
    with service_outage.watch() as seen:
        assert br._find_in_nara({}, "RG 407", "", "", None, {}) == []
    assert seen == ["nara"]


@pytest.mark.parametrize("script,alerted", [("phase3_enrich_data.py", True),
                                            ("phase2_extract.py", False)])  # fmt: skip
def test_a_nara_key_that_did_not_load_is_alerted_in_phase3(
    monkeypatch, script, alerted
):
    import ecs_entrypoint as ee

    monkeypatch.setattr(ee, "_current_phase_script", script)
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append((s, e, k)))
    ee._alert_nara_key_missing("dev-wwii-pipeline/nara-api-key", RuntimeError("denied"))
    assert bool(sent) is alerted
    if alerted:
        assert sent[0][:2] == ("nara", "key_unavailable")
        assert "unresolved" in sent[0][2]["note"]


# --- DynamoDB ------------------------------------------------------------------------------------


def test_a_dynamodb_outage_on_a_url_claim_is_counted_and_a_taken_url_is_not(
    monkeypatch,
):
    from src.utils import url_registry as ur

    table = mock.Mock()
    table.put_item.side_effect = RuntimeError("ProvisionedThroughputExceeded")
    monkeypatch.setattr(ur, "_table", lambda: table)
    with service_outage.watch() as seen:
        assert ur.claim("https://example.org/page") is False
    assert seen == ["dynamodb"]

    class Taken(Exception):
        response = {"Error": {"Code": "ConditionalCheckFailedException"}}

    table.put_item.side_effect = Taken()
    monkeypatch.setattr(ur, "_load_raw", lambda t, c: (None, None))
    with service_outage.watch() as seen:
        assert ur.claim("https://example.org/page") is False
    assert seen == []  # someone else holds it: not an outage


def test_a_search_cache_outage_is_counted():
    from src.utils.search_cache import _DynamoBackend

    backend = _DynamoBackend.__new__(_DynamoBackend)
    backend._table = mock.Mock()
    backend._table.get_item.side_effect = RuntimeError("down")
    backend._table.put_item.side_effect = RuntimeError("down")
    with service_outage.watch() as seen:
        assert backend.get("k") is None
        backend.put("k", "v")
    assert seen == ["dynamodb"]


# --- image hosts --------------------------------------------------------------------------------


def test_an_image_host_outage_uses_no_vision_attempt(monkeypatch, tmp_path):
    from src.extraction import image_map_vision as imv
    import src.extraction.images as images

    monkeypatch.setattr(
        images,
        "fetch_image_bytes_with_reason",
        lambda url: (None, None, "bad_status_transient"),
    )
    rec = {"url": "https://example.org/map.jpg", "map_vision_attempts": 1}
    assert imv._vision_step(rec, tmp_path, object()) == "host_unavailable"
    assert rec["map_vision_attempts"] == 1 and rec["host_unavailable_runs"] == 1


def test_a_host_that_never_answers_goes_to_a_human(monkeypatch, tmp_path):
    """A host answering 503 on purpose must not keep an image pending for ever."""
    from src.extraction import image_map_vision as imv
    import src.extraction.images as images

    monkeypatch.setattr(
        images,
        "fetch_image_bytes_with_reason",
        lambda url: (None, None, "bad_status_transient"),
    )
    rec = {"url": "https://example.org/map.jpg"}
    outcomes = [
        imv._vision_step(rec, tmp_path, object())
        for _ in range(imv.MAX_HOST_UNAVAILABLE_RUNS)
    ]
    assert outcomes[-1] == "needs_human" and rec["needs_review"] is True
    assert outcomes[:-1] == ["host_unavailable"] * (imv.MAX_HOST_UNAVAILABLE_RUNS - 1)


def test_a_timeout_is_an_outage_and_a_refused_connection_a_dead_link(monkeypatch):
    import requests

    import src.extraction.images as images
    import src.utils.url_hygiene as uh

    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda h: ["93.184.216.34"])
    for exc, reason in (
        (requests.Timeout(), "fetch_timeout"),
        (requests.ConnectionError(), "fetch_error"),
    ):
        monkeypatch.setattr(uh, "_pinned_get", mock.Mock(side_effect=exc))
        assert (
            images._safe_image_response_with_reason("https://e.org/x.jpg")[1] == reason
        )
    assert "fetch_timeout" in images.TRANSIENT_FETCH_REASONS
    assert "fetch_error" not in images.TRANSIENT_FETCH_REASONS


def test_an_outage_resolution_is_not_reused_by_a_duplicate_citation(monkeypatch):
    from src.enrichment import bibliography_resolver as br

    calls = []

    def resolve(data, grok, config):
        calls.append(1)
        service_outage.failed("archive_org", "candidates", "Timeout")
        data["search_status"] = "not_found"

    monkeypatch.setattr(br, "resolve_bibliography_entry", resolve)
    monkeypatch.setattr(br, "_citation_key", lambda d: "same-citation")
    cache: dict = {}
    first: dict = {}
    second: dict = {}
    br._resolve_watched(first, None, {}, cache)
    br._resolve_watched(second, None, {}, cache)
    assert (
        len(calls) == 2
    )  # the duplicate searched again instead of reusing the outage miss
    assert first.get("_backend_down") and second.get("_backend_down")


def test_a_dead_image_link_still_counts_as_an_attempt(monkeypatch, tmp_path):
    from src.extraction import image_map_vision as imv
    import src.extraction.images as images

    monkeypatch.setattr(
        images, "fetch_image_bytes_with_reason", lambda url: (None, None, "bad_status")
    )
    monkeypatch.setattr(imv, "vision_is_map", lambda b, c, g: (None, None))
    rec = {"url": "https://example.org/gone.jpg", "map_vision_attempts": 0}
    assert imv._vision_step(rec, tmp_path, object()) == "undecided"
    assert rec["map_vision_attempts"] == 1


@pytest.mark.parametrize("code,reason", [(503, "bad_status_transient"),
                                         (429, "bad_status_transient"),
                                         (404, "bad_status")])  # fmt: skip
def test_the_image_fetch_tells_an_outage_from_a_dead_link(monkeypatch, code, reason):
    import src.extraction.images as images
    import src.utils.url_hygiene as uh

    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(uh, "_pinned_get", lambda *a, **k: _resp(code))
    assert images._safe_image_response_with_reason("https://example.org/x.jpg") == (
        None,
        reason,
    )


# --- S3 ------------------------------------------------------------------------------------------


def test_the_wrapper_s3_client_retries(monkeypatch):
    import ecs_entrypoint as ee

    made = {}
    monkeypatch.setattr(ee, "_s3", {})  # no client cached by an earlier test
    monkeypatch.setattr(
        ee.boto3, "client", lambda svc, **k: made.update(k) or mock.Mock()
    )
    ee._s3_client()
    ee._s3_client()
    cfg = made["config"]
    assert cfg.retries == {"mode": "adaptive", "max_attempts": 10}


def _sync(monkeypatch, tmp_path, fail):
    import ecs_entrypoint as ee

    out = tmp_path / "output" / "places"
    out.mkdir(parents=True)
    for n in range(3):
        f = out / f"p{n}.json"
        f.write_text("{}")
        os.utime(f, (1, 1))
    s3 = mock.Mock()
    s3.upload_file.side_effect = lambda src, b, key: (
        (_ for _ in ()).throw(RuntimeError("SlowDown")) if fail(key) else None
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    monkeypatch.setattr(ee, "_heartbeat_nat_lease", lambda: None)
    sync = ee.BackgroundSync.__new__(ee.BackgroundSync)
    sync._interval = 120
    sync._uploaded_mtimes = {}
    sync._last_activity_time = 0.0  # long idle: the watchdog would fire
    sync.progress = mock.Mock()
    return ee, sync, s3


def test_one_failed_upload_does_not_stop_the_pass(monkeypatch, tmp_path):
    ee, sync, s3 = _sync(monkeypatch, tmp_path, lambda key: key.endswith("p0.json"))
    sync._upload_changed()
    assert s3.upload_file.call_count == 3  # the other two still went
    assert sync._s3_failing_passes == 1


def test_s3_failing_alerts_once_and_is_not_a_stall(monkeypatch, tmp_path):
    ee, sync, s3 = _sync(monkeypatch, tmp_path, lambda key: True)
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append(e))
    killed = []
    monkeypatch.setattr(ee.os, "kill", lambda *a: killed.append(a))
    for _ in range(sync.S3_ALERT_AFTER_PASSES + 2):
        sync._sync()
    assert sent == ["sync_failing"]
    assert (
        killed == []
    )  # S3 is why nothing uploaded: the watchdog must not kill the task


def test_a_merged_record_left_in_s3_is_alerted_with_how_to_remove_it(monkeypatch):
    import ecs_entrypoint as ee

    s3 = mock.Mock()
    s3.delete_object.side_effect = RuntimeError("AccessDenied")
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee, "_deleted_keys", {"output/people/a.json"})
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append((e, k)))
    ee._delete_merged_from_s3()
    [(event, fields)] = sent
    assert event == "merged_delete_failed" and fields["count"] == 1
    # the untrusted keys go to a manifest, never into a command to paste
    assert "aws s3 rm" not in fields["note"]
    assert "manifests/merged_delete_left/" in fields["note"]
    [put] = s3.put_object.call_args_list
    assert json.loads(put.kwargs["Body"]) == ["output/people/a.json"]


def test_a_forged_key_cannot_forge_a_log_line(monkeypatch, caplog):
    import ecs_entrypoint as ee

    s3 = mock.Mock()
    s3.delete_object.side_effect = RuntimeError("AccessDenied")
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(
        ee, "_deleted_keys", {"output/people/x.json\nOBSERV fake status=ok"}
    )
    ee._delete_merged_from_s3()
    assert not any(line.startswith("OBSERV fake") for line in caplog.text.splitlines())


def test_a_long_s3_failure_lets_the_watchdog_act(monkeypatch, tmp_path):
    ee, sync, s3 = _sync(monkeypatch, tmp_path, lambda key: True)
    monkeypatch.setattr(ee, "observe_error", lambda *a, **k: None)
    monkeypatch.setattr(ee, "_notify_failure", lambda *a, **k: None)
    monkeypatch.setattr(ee, "_mark_watchdog_stop", lambda: None)
    killed = []
    monkeypatch.setattr(ee.os, "kill", lambda *a: killed.append(a))
    sync._s3_failing_passes = sync.WATCHDOG_TIMEOUT // sync._interval
    sync._stop = mock.Mock()
    sync._sync()
    assert killed  # S3 down for the whole watchdog window: the watchdog acts as before


def test_a_vanished_file_is_not_an_s3_failure(monkeypatch, tmp_path):
    ee, sync, s3 = _sync(monkeypatch, tmp_path, lambda key: False)
    s3.upload_file.side_effect = FileNotFoundError("merged away")
    sync._upload_changed()
    assert sync._s3_failing_passes == 0
