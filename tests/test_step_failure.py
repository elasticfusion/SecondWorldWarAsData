"""Owner 2026-11-18: a Phase 3 step that raised and stopped partway must not let the book count
as finished. Definition: a step failure = the step raised (part of its work was never
attempted); a record failure (one record left unmarked, retried next run) does not block the
book; a run stop (Grok/OpenSERP down, no credits) is handled by the deferral. Data corruption
(the book's OWN stored record is unreadable: stop_cause.RecordCorrupt) errors out loudly and
holds the book; any other step failure - including a service answer that does not parse - leaves
the book queued for retry (attempt-capped).
"""

import json
import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402
from src.utils import entity_records, stop_cause  # noqa: E402
from tests.test_phase3_lock_release import _stub_run_phase_to_failure  # noqa: E402


def _phase3():
    import importlib

    return importlib.import_module("phase3_enrich_data")


@pytest.mark.parametrize(
    "stats,code",
    [({"a": {"status": "ok"}}, 0),
     ({"a": {"status": "ok"}, "b": {"status": "error", "corrupt": False}},
      stop_cause.EXIT_STEP_FAILED),
     ({"a": {"status": "error", "corrupt": False}, "b": {"status": "error", "corrupt": True}},
      stop_cause.EXIT_DATA_CORRUPT)],
)  # fmt: skip
def test_phase3_exit_code_follows_its_steps(stats, code):
    assert _phase3()._exit_for_steps(stats) == code


def test_a_corrupt_stored_record_is_data_corruption(tmp_path):
    bad = tmp_path / "place.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(stop_cause.RecordCorrupt) as e:
        entity_records.read_record(bad)
    assert stop_cause.is_data_corruption(e.value)
    assert isinstance(
        e.value, ValueError
    )  # existing `except ValueError` handlers still match
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(stop_cause.RecordCorrupt):
        entity_records.read_record(bad)
    good = tmp_path / "ok.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    assert entity_records.read_record(good) == {"a": 1}


def test_a_missing_record_is_not_corruption(tmp_path):
    with pytest.raises(OSError) as e:
        entity_records.read_record(tmp_path / "gone.json")
    assert not stop_cause.is_data_corruption(e.value)


def test_corruption_survives_a_wrapping_error():
    try:
        try:
            raise stop_cause.RecordCorrupt("output/places/x.json", "JSONDecodeError")
        except ValueError as inner:
            raise RuntimeError("step failed") from inner
    except RuntimeError as outer:
        assert stop_cause.is_data_corruption(outer)
    try:
        try:
            raise stop_cause.RecordCorrupt("x", "y")
        except ValueError:
            # set aside, then an unrelated error in the handler: not corruption
            raise KeyError("implicit context")  # noqa: B904 - the chain under test
    except KeyError as outer:
        assert not stop_cause.is_data_corruption(outer)


def _service_errors():
    """Errors from untrusted service content: never corruption (a retry can fix them)."""
    import requests

    errors = [KeyError("x"), TypeError("x"), ValueError("bad value"), OSError("x")]
    try:
        json.loads("<html>502 Bad Gateway</html>")
    except ValueError as e:
        errors.append(e)
    try:
        requests.models.complexjson.loads("<html>")
    except ValueError as e:
        errors.append(requests.JSONDecodeError(str(e), "<html>", 0))
    errors.append(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"))
    jsonschema = pytest.importorskip("jsonschema")
    try:
        jsonschema.validate({"a": 1}, {"properties": {"a": {"type": "string"}}})
    except jsonschema.ValidationError as e:
        errors.append(e)
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        a: int

    try:
        M.model_validate({"a": "grok said no"})
    except ValidationError as e:
        errors.append(e)
    return errors


@pytest.mark.parametrize("error", _service_errors(), ids=lambda e: type(e).__name__)
def test_a_service_answer_that_does_not_parse_is_a_step_failure(error):
    p3 = _phase3()
    stats: dict = {}

    def step():
        raise error

    assert p3.run_enrichment_step(stats, "geocode", step) == 0
    assert stats["geocode"]["status"] == "error"
    assert stats["geocode"]["corrupt"] is False
    assert p3._exit_for_steps(stats) == stop_cause.EXIT_STEP_FAILED


def test_the_step_runner_marks_a_step_stopped_by_a_corrupt_record(tmp_path):
    p3 = _phase3()
    bad = tmp_path / "x.json"
    bad.write_text("{", encoding="utf-8")
    stats: dict = {}

    def step():
        for f in [bad]:
            entity_records.read_record(f)
        return 1

    assert p3.run_enrichment_step(stats, "places", step) == 0
    assert stats["places"]["corrupt"] is True
    assert "x.json" in stats["places"]["error"]
    assert p3._exit_for_steps(stats) == stop_cause.EXIT_DATA_CORRUPT


def test_the_step_runner_counts_a_step_that_finished():
    p3 = _phase3()
    stats: dict = {}
    assert p3.run_enrichment_step(stats, "weather", lambda: 7) == 7
    assert stats == {"weather": {"enriched": 7, "status": "ok"}}


def test_a_step_failure_is_counted_and_the_book_stays_queued(monkeypatch):
    monkeypatch.setattr(
        ee,
        "_phase_results",
        lambda: {"errored_sources": ["geocode", "openserp_people"]},
    )
    called = []
    monkeypatch.setattr(ee, "_queue_followup", lambda *a: called.append(a) or "x")
    note = ee._defer_on_stop_cause(stop_cause.EXIT_STEP_FAILED)
    assert "geocode, openserp_people" in note and "stays queued" in note
    assert called == []  # not deferred: an ordinary, counted failure (retried, capped)
    assert stop_cause.cause_for_exit(stop_cause.EXIT_STEP_FAILED) is None


def _corrupt_results():
    return {
        "errored_sources": ["geocode", "places"],
        "source_stats": {
            "geocode": {"status": "error", "corrupt": False, "error": "timeout"},
            "places": {
                "status": "error",
                "corrupt": True,
                "error": "stored record output/places/x.json is corrupt: " + "z" * 500,
            },
        },
    }


def test_data_corruption_holds_the_book_and_alerts_loudly(monkeypatch):
    monkeypatch.setattr(ee, "_phase_results", _corrupt_results)
    alerts, held = [], []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: alerts.append((e, k)))
    monkeypatch.setattr(
        ee, "_queue_followup", lambda cause, reason: held.append(cause) or "HELD"
    )
    assert ee._defer_on_stop_cause(stop_cause.EXIT_DATA_CORRUPT) == "HELD"
    assert held == ["failure"]  # held for the operator, not auto-resumed
    [(event, fields)] = alerts
    assert event == "data_corrupt" and "DATA CORRUPTION" in fields["note"]
    assert fields["steps"] == "places"  # only the corrupt step, not the timed-out one
    assert "output/places/x.json" in fields["error"] and len(fields["error"]) <= 400


@pytest.mark.parametrize(
    "code", [stop_cause.EXIT_STEP_FAILED, stop_cause.EXIT_DATA_CORRUPT]
)
def test_run_phase_with_a_step_exit_alerts_syncs_and_frees_the_lock(monkeypatch, code):
    order: list = []
    _stub_run_phase_to_failure(monkeypatch, order)
    from unittest import mock

    monkeypatch.setattr(
        ee.subprocess, "run", lambda *a, **k: mock.Mock(returncode=code)
    )
    monkeypatch.setattr(ee, "_phase_results", _corrupt_results)
    monkeypatch.setattr(ee, "observe_error", lambda *a, **k: order.append("loud"))
    monkeypatch.setattr(ee, "_queue_followup", lambda *a: order.append("held") or "")
    with pytest.raises(SystemExit) as stop:
        ee.run_phase("phase3_enrich_data.py", [])
    assert stop.value.code == code
    tail = ["alert", "sync", "lock", "nat"]
    assert order == (["loud", "held"] + tail if code == 83 else tail)


def _geocode_with(monkeypatch, tmp_path, outcome):
    """Run the places+geocode step with the geocoder stubbed to ``outcome`` (a report, or an
    exception to raise); returns its source_stats."""
    from types import SimpleNamespace

    import src.enrichment.hill_geocode as hill
    import src.enrichment.nominatim_geocode as nom
    import src.enrichment.places_grok_geocode as pg

    p3 = _phase3()

    def geocode(*a, **k):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(pg, "geocode_places_dir", geocode)
    monkeypatch.setattr(pg, "cascade_geocoder", lambda *a: None)
    monkeypatch.setattr(pg, "configure_elevation", lambda *a: None)
    monkeypatch.setattr(nom, "make_nominatim_geocoder", lambda *a: None)
    monkeypatch.setattr(hill, "make_hill_geocoder", lambda *a: None)
    monkeypatch.setattr(p3, "_update_lock_status", lambda *a: None)
    monkeypatch.setattr(p3, "load_config", lambda: {})
    args = SimpleNamespace(output_dir=tmp_path, cache_dir=tmp_path, max_items=0)
    stats: dict = {}
    p3._enrich_places_and_geocode(args, None, 1, lambda n, f: 0, stats)
    return p3, stats


def test_per_place_geocode_errors_do_not_fail_the_step(monkeypatch, tmp_path):
    """Per-record errors are record failures (retried next run): only a raise fails the step."""
    from types import SimpleNamespace

    report = SimpleNamespace(
        attempted=10, geocoded=6, not_found=1, low_confidence=1, errors=2
    )
    p3, stats = _geocode_with(monkeypatch, tmp_path, report)
    assert stats["geocode"]["status"] == "ok" and stats["geocode"]["errors"] == 2
    assert p3._exit_for_steps(stats) == 0


def test_a_geocode_step_that_raises_fails_the_step(monkeypatch, tmp_path):
    p3, stats = _geocode_with(monkeypatch, tmp_path, RuntimeError("boom\nOBSERV fake"))
    assert stats["geocode"]["status"] == "error"
    assert "\n" not in stats["geocode"]["error"]  # control chars stripped (log forging)
    assert p3._exit_for_steps(stats) == stop_cause.EXIT_STEP_FAILED
