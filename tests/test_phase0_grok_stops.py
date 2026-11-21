"""Owner 2026-11-20 (task 1.2f): in Phase 0 the markdown repair is OPTIONAL - a Grok outage or
no credit sends the document to human review like any failed repair; the translation is
REQUIRED - an untranslated document must not go on, so the run stops (exit 81/80) and the alert
names what to repeat (the upload for an import, the OCR re-drive for the merge/cross-check).
"""

import os
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402
from src.ingestion import ocr_chunk_manifest as cm  # noqa: E402
from src.utils import grok_models as G  # noqa: E402
from src.utils import stop_cause  # noqa: E402
from tests.test_ocr_merge_chunks import _book, _merge, mh  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("stop", [G.GrokUnreachable, G.GrokFundsExhausted])
def test_the_markdown_repair_is_optional(monkeypatch, stop):
    from src.ingestion import md_correction as mc

    cleared = []
    monkeypatch.setattr(mc, "clear_transient_stop", lambda: cleared.append(1))
    client = mock.Mock()
    client.chat_completion.side_effect = stop("down")
    assert mc.correct_markdown("x" * 500, grok_client=client, source_hint="B") is None
    assert cleared == [
        1
    ]  # the import goes on (and may ask Grok again for the translation)


def test_a_missing_model_still_stops_the_repair():
    from src.ingestion import md_correction as mc

    client = mock.Mock()
    client.chat_completion.side_effect = G.GrokModelUnavailable("gone")
    with pytest.raises(G.GrokModelUnavailable):
        mc.correct_markdown("x" * 500, grok_client=client)


@pytest.mark.parametrize(
    "script", ["phase0_convert.py", "phase0_ocr_crosscheck.py", "phase0_video.py"]
)
def test_phase0_scripts_exit_with_the_stop_code(script):
    src = (ROOT / script).read_text(encoding="utf-8")
    assert "sys.exit(stop_cause.run_main(main))" in src


def test_run_main_turns_a_grok_stop_into_its_exit_code():
    def down():
        raise G.GrokUnreachable("x")

    def broke():
        raise G.GrokFundsExhausted("x")

    assert stop_cause.run_main(down) == stop_cause.EXIT_CONNECTIVITY
    assert stop_cause.run_main(broke) == stop_cause.EXIT_FUNDING


def test_an_import_stop_names_the_upload_to_repeat(monkeypatch):
    monkeypatch.setattr(ee, "_current_phase_script", "phase0_convert.py")
    monkeypatch.setenv(
        "CONVERT_KEY", "contentrepository/books/Patton at the Bulge.epub"
    )
    note = ee._queue_followup("connectivity", "why")
    assert "re-upload the same file under the same name" in note
    assert f"s3://{ee.BUCKET}/contentrepository/books/Patton at the Bulge.epub" in note


def test_a_crosscheck_stop_names_the_ocr_redrive(monkeypatch):
    monkeypatch.setattr(ee, "_current_phase_script", "phase0_ocr_crosscheck.py")
    monkeypatch.delenv("CONVERT_KEY", raising=False)
    monkeypatch.delenv("VIDEO_KEY", raising=False)
    monkeypatch.setenv("BOOK_NAME", "B")
    assert cm.redrive_command("B") in ee._queue_followup("funding", "why")


@pytest.mark.parametrize("stop", [G.GrokUnreachable, G.GrokFundsExhausted])
def test_an_ocr_merge_stopped_by_grok_frees_its_claim_and_says_how_to_redrive(
    mh, monkeypatch, stop  # noqa: F811
):
    alerts = []
    monkeypatch.setattr(mh, "observe_error", lambda s, e, **k: alerts.append((e, k)))

    def translate(md, **k):
        raise stop("down")

    # mock.patch, not monkeypatch: the mh fixture already patches this name, and monkeypatch
    # would restore ITS patch after the fixture has undone it (leaking into later tests)
    with mock.patch(
        "src.ingestion.translation.normalize_markdown_to_english", translate
    ):
        s3 = _book()
        assert _merge(mh, s3) == ""  # nothing published, no raise (Lambda would retry)
        assert not s3.keys("contentrepository/")
        [(event, fields)] = alerts
        assert event == "grok_unavailable" and cm.redrive_command("B") in fields["note"]
        assert fields["cause"] == (
            "funding" if stop is G.GrokFundsExhausted else "connectivity"
        )
        # the claim is free: a re-drive merges again
        released = []
        monkeypatch.setattr(cm, "release_merge", lambda *a: released.append(a) or True)
        assert _merge(mh, s3) == ""
        assert released  # the second attempt got as far as translating again
