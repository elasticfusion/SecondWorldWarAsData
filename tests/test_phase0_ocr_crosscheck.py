"""The Phase 0 cross-check task: publishes an OCR'd book whose upload has a validation URL,
corrected against it; a failure is loud, releases the merge claim and exits 1."""

from unittest.mock import patch

import phase0_ocr_crosscheck as task
from src.ingestion import ocr_chunk_manifest as cm
from tests.fake_s3 import FakeS3

WORDS = (
    "The division landed on the beach at dawn and moved inland toward the town of Carentan "
    "where the Schwarzwalder regiment held the bridge "
)
BODY = (WORDS * 6).strip()
SEP = "\n\n1" + "-" * 48 + "\n\n"
URL = "https://h.org/report.html"
HTML = f"<html><body><p>{BODY}</p><p>{BODY}</p></body></html>"


def _s3(url=URL):
    ocr = BODY.replace("Schwarzwalder", "Schwarzwaldor", 1) + SEP + BODY
    m = cm.build_manifest("s1", [""], "contentrepository/R/R.pdf", url)
    return FakeS3(
        {
            cm.manifest_key("R"): m.to_json(),
            cm.chunk_markdown_key("R", ""): ocr,
            cm.merged_key("R"): "s1",
        }
    )


def _env(monkeypatch):
    monkeypatch.setattr(task, "BUCKET", "buck")
    monkeypatch.setenv("BOOK_NAME", "R")
    monkeypatch.setenv("SUBMISSION_ID", "s1")


def test_the_book_is_corrected_and_published(monkeypatch):
    _env(monkeypatch)
    s3 = _s3()
    with (
        patch(
            "src.ingestion.ocr_crosscheck.fetch_html",
            lambda u: HTML if u == URL else None,
        ),
        patch(
            "src.ingestion.translation.normalize_markdown_to_english",
            lambda md, **k: md,
        ),
        patch("src.ingestion.review_reject.reject_to_review"),
        patch.object(task.boto3, "client", return_value=s3),
    ):
        assert task.main() == 0
    content = s3.text("contentrepository/R/chapter1/chapter1-content.md")
    assert "Schwarzwaldor" not in content and "Schwarzwalder" in content
    assert '"applied": 1' in s3.text("ocr-output/R/merged/postprocess.json")


def test_a_failure_alerts_releases_the_claim_and_exits_1(monkeypatch):
    _env(monkeypatch)
    s3 = _s3()
    with (
        patch("src.ingestion.ocr_promote.promote", side_effect=RuntimeError("boom")),
        patch.object(task.boto3, "client", return_value=s3),
        patch("src.utils.observe.observe_error") as alert,
    ):
        assert task.main() == 1
    assert alert.call_args[0][:2] == ("phase0_crosscheck", "failed")
    assert cm.merged_key("R") not in s3.keys()
    assert not s3.keys("contentrepository/")


def test_a_resubmitted_book_is_not_published_by_the_old_task(monkeypatch):
    """The book was re-OCR'd after this task was launched: the newer submission's own merge
    publishes it; this task neither publishes nor releases the newer claim."""
    _env(monkeypatch)
    monkeypatch.setenv("SUBMISSION_ID", "s0")
    s3 = _s3()
    with patch.object(task.boto3, "client", return_value=s3):
        assert task.main() == 0
    assert not s3.keys("contentrepository/") and cm.merged_key("R") in s3.keys()


def test_a_book_resubmitted_during_the_run_is_not_published_by_the_old_task(
    monkeypatch,
):
    _env(monkeypatch)
    s3 = _s3()
    real = task._require_current

    def resubmitted_midway(s3_, bucket, book, submission):
        cm.write_manifest(s3_, bucket, book, cm.build_manifest("s2", [""]))
        real(s3_, bucket, book, submission)

    with (
        patch("src.ingestion.ocr_crosscheck.fetch_html", lambda u: HTML),
        patch(
            "src.ingestion.translation.normalize_markdown_to_english",
            lambda md, **k: md,
        ),
        patch("src.ingestion.review_reject.reject_to_review"),
        patch.object(task, "_require_current", resubmitted_midway),
        patch.object(task.boto3, "client", return_value=s3),
    ):
        assert task.main() == 0  # superseded is not a failure ...
    assert not s3.keys("contentrepository/")  # ... and publishes nothing
    assert cm.read_manifest(s3, "buck", "R").submission == "s2"


def test_a_failure_names_the_redrive_command(monkeypatch):
    _env(monkeypatch)
    s3 = _s3()
    with (
        patch("src.ingestion.ocr_promote.promote", side_effect=RuntimeError("boom")),
        patch.object(task.boto3, "client", return_value=s3),
        patch("src.utils.observe.observe_error") as alert,
    ):
        task.main()
    assert "-wwii-ocr-merge" in alert.call_args.kwargs["note"]
    assert "chandra-R" in alert.call_args.kwargs["note"]


def test_missing_chunks_are_a_failure(monkeypatch):
    _env(monkeypatch)
    s3 = _s3()
    s3.delete_object(Bucket="buck", Key=cm.chunk_markdown_key("R", ""))
    with (
        patch.object(task.boto3, "client", return_value=s3),
        patch("src.utils.observe.observe_error"),
    ):
        assert task.main() == 1


def test_a_bad_book_name_is_refused(monkeypatch):
    monkeypatch.setattr(task, "BUCKET", "buck")
    monkeypatch.setenv("BOOK_NAME", "")
    with patch("src.utils.observe.observe_error") as alert:
        assert task.main() == 2
    assert alert.call_args[0][:2] == ("phase0_crosscheck", "bad_input")


# --- container wiring -----------------------------------------------------------------------------


def test_the_entrypoint_lets_the_task_fetch_its_own_inputs(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    import ecs_entrypoint

    monkeypatch.setenv("PIPELINE_PHASE", "phase0_ocr_crosscheck")
    monkeypatch.delenv("CONVERT_KEY", raising=False)
    monkeypatch.delenv("VIDEO_KEY", raising=False)
    with patch.object(ecs_entrypoint, "_download_phase1_inputs") as book_sync:
        ecs_entrypoint._download_phase0_inputs()
    book_sync.assert_not_called()  # no contentrepository/{book} sync for a book not yet there


def test_the_entrypoint_brings_nat_up_for_the_cross_check(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("NAT_READY_MAX_ATTEMPTS", "1")
    import ecs_entrypoint

    with patch.object(ecs_entrypoint.boto3, "client") as client:
        client.return_value.invoke.side_effect = RuntimeError("stop here")
        with patch.object(ecs_entrypoint.time, "sleep"):
            try:
                ecs_entrypoint._ensure_nat_if_needed("phase0_ocr_crosscheck.py")
            except (Exception, SystemExit):  # noqa: BLE001 - only whether NAT was asked
                pass
        assert client.return_value.invoke.called  # it fetches the validation URL
        client.reset_mock()
        ecs_entrypoint._ensure_nat_if_needed("phase0_convert.py")
        assert not client.return_value.invoke.called


def test_the_image_and_the_stack_carry_the_task():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert "phase0_ocr_crosscheck.py" in (root / "Dockerfile").read_text()
    compute = (root / "cloudformation" / "compute.yaml").read_text()
    assert "Command: ['phase0_ocr_crosscheck.py']" in compute
    assert (
        "PHASE0_CROSSCHECK_TASK_DEF: !Sub ${EnvironmentName}-wwii-phase0-crosscheck"
        in compute
    )
