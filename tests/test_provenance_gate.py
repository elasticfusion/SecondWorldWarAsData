"""The end-of-Phase-2 provenance gate (ecs_entrypoint._provenance_gate / _phase3_gate_decision):
validates the task's book against the post-sync corpus, uploads the report, and - in ``block``
mode - holds Phase 3 when a hard check fails. Fail-closed: a validation that cannot run does not
pass."""

import os
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as ee  # noqa: E402


@pytest.fixture(autouse=True)
def _no_s3(monkeypatch, tmp_path):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    monkeypatch.setattr(ee, "_refresh_validation_corpus", mock.Mock())
    monkeypatch.setattr(ee, "_s3_client", mock.Mock())


def _findings(ok):
    return SimpleNamespace(ok=ok, failed={"P2-Q1": 0 if ok else 3})


def test_no_book_is_skipped_loudly(monkeypatch):
    monkeypatch.delenv("BOOK_NAME", raising=False)
    with mock.patch.object(ee, "observe_warn") as warn:
        assert ee._provenance_gate("2") == (True, "no book to validate")
    assert warn.call_args.args[:2] == ("provenance", "validation_skipped")


def test_the_book_is_validated_on_the_refreshed_corpus_and_the_report_uploaded(
    monkeypatch, tmp_path
):
    from scripts import validate_provenance as vp

    monkeypatch.setenv("BOOK_NAME", "B")
    report = tmp_path / "r.json"
    with mock.patch.object(
        vp, "validate_and_record", return_value=(_findings(False), report)
    ) as run:
        passed, reason = ee._provenance_gate("2")
    assert not passed and "3 hard finding" in reason
    ee._refresh_validation_corpus.assert_called_once_with("B", vp.TEXT_CITING_ENTITIES)
    assert run.call_args.args[1:] == ("B", "2")
    upload = ee._s3_client.return_value.upload_file
    assert upload.call_args.args[2] == "output/reports/provenance/B-phase2.json"


def test_a_report_upload_failure_is_not_a_failed_validation(monkeypatch, tmp_path):
    from scripts import validate_provenance as vp

    monkeypatch.setenv("BOOK_NAME", "B")
    ee._s3_client.return_value.upload_file.side_effect = RuntimeError("s3 down")
    with (
        mock.patch.object(
            vp, "validate_and_record", return_value=(_findings(True), tmp_path / "r")
        ),
        mock.patch.object(ee, "observe_error") as err,
    ):
        assert ee._provenance_gate("2") == (True, "provenance validation passed")
    assert err.call_args.args[:2] == ("provenance", "report_upload_failed")


@pytest.mark.parametrize(
    "held_by, subject, link",
    [
        (["provenance"], "WWII Pipeline: provenance review needed", False),
        (["dedup"], "WWII Pipeline: dedup review needed", True),
        (
            ["dedup", "provenance"],
            "WWII Pipeline: dedup + provenance review needed",
            True,
        ),
    ],
)
def test_the_gate_alert_names_what_holds_phase3(monkeypatch, held_by, subject, link):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setenv("DEDUP_REVIEW_URL", "https://review")
    sns = mock.Mock()
    with (
        mock.patch.object(ee.boto3, "client", return_value=sns),
        mock.patch.object(ee, "_topic_has_confirmed_sub", return_value=True),
    ):
        ee._notify_dedup_gate("block", "why", held_by)
    kwargs = sns.publish.call_args.kwargs
    assert kwargs["Subject"] == subject
    assert ("Dedup Review UI" in kwargs["Message"]) == link


def test_a_validation_that_cannot_run_fails_closed(monkeypatch):
    from scripts import validate_provenance as vp

    monkeypatch.setenv("BOOK_NAME", "B")
    with (
        mock.patch.object(vp, "validate_and_record", side_effect=RuntimeError("x")),
        mock.patch.object(ee, "observe_error") as err,
    ):
        passed, reason = ee._provenance_gate("2")
    assert not passed and "could not run" in reason
    assert err.call_args.args[:2] == ("provenance", "validation_failed")


@pytest.mark.parametrize(
    "dedup, provenance, mode, expected",
    [
        (("auto_proceed", "clean"), (True, "ok"), "block", "auto_proceed"),
        (("auto_proceed", "clean"), (False, "prov failed"), "warn", "auto_proceed"),
        (("auto_proceed", "clean"), (False, "prov failed"), "block", "block"),
        (("block", "dups pending"), (False, "prov failed"), "block", "block"),
        (("block", "dups pending"), (True, "ok"), "block", "block"),
    ],
)
def test_the_phase3_gate(monkeypatch, dedup, provenance, mode, expected):
    monkeypatch.setenv("PROVENANCE_GATE", mode)
    monkeypatch.setattr(ee, "_dedup_gate_decision", lambda ok: dedup)
    action, reason, held_by = ee._phase3_gate_decision(True, provenance)
    assert action == expected
    assert ("provenance" in held_by) == (mode == "block" and not provenance[0])
    assert ("dedup" in held_by) == (dedup[0] == "block")
    if not provenance[0]:
        assert "prov failed" in reason  # the operator sees why, in either mode
