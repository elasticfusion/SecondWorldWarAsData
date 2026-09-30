"""Tests for the video processor (phase0_video) + Grok vision analyzer.

ffmpeg, Grok STT, Grok vision, and S3 are all mocked — this proves the 9d
orchestration (transcribe -> roster -> perceive -> resolve -> chapter markdown +
provenance + source retention) without binaries/network."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("S3_BUCKET", "dev-wwii-data-pipeline")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import phase0_video as pv
from src.ingestion.web_video import TranscriptSegment


def test_book_from_key():
    assert (
        pv._book_from_key("contentrepository/BulgeVideo/Battle of the Bulge.mp4")
        == "Battle_of_the_Bulge"
    )


def test_unsupported_extension_skips():
    with patch.object(pv.boto3, "client", return_value=MagicMock()):
        assert pv.process_video("x/y/thing.pdf") == ""


def _segs():
    return [
        TranscriptSegment(start=10.0, end=15.0, text="The Bulge began.", speaker="1"),
        TranscriptSegment(start=20.0, end=25.0, text="Bradley responded.", speaker="2"),
    ]


def test_process_video_writes_chapter_structure_with_provenance():
    """Happy path: audio+STT+resolve -> writes meta+content under chapter1/ with
    source_video_key + speaker_id_version provenance; SOURCE video retained."""
    s3 = MagicMock()
    s3.download_file.return_value = None
    with (
        patch.object(pv.boto3, "client", return_value=s3),
        patch.object(pv, "_extract_audio", return_value=True),
        patch("src.ingestion.web_video.GrokTranscriber") as MockT,
        patch("src.ingestion.video_roster.build_roster", return_value=[]),
        patch("src.ingestion.video_roster.WikipediaAppearanceLookup"),
        patch(
            "src.ingestion.video_perception.identify_speakers",
            return_value=[
                {
                    "timecode": "00:00:10-00:00:15",
                    "name": "Omar N. Bradley",
                    "role": "narrator",
                    "needs_review": False,
                },
                {
                    "timecode": "00:00:20-00:00:25",
                    "name": None,
                    "role": "unknown",
                    "needs_review": True,
                },
            ],
        ),
        patch.object(pv, "_vision_analyzer", return_value=MagicMock()),
        patch.object(pv, "_frame_sampler", return_value=MagicMock()),
    ):
        MockT.return_value.transcribe.return_value = _segs()
        out = pv.process_video("contentrepository/BulgeVideo/Battle of the Bulge.mp4")

    assert out == "contentrepository/Battle_of_the_Bulge/chapter1/chapter1-content.md"
    keys = [c.kwargs["Key"] for c in s3.put_object.call_args_list]
    assert keys == [
        "contentrepository/Battle_of_the_Bulge/chapter1/chapter1-meta.yaml",
        "contentrepository/Battle_of_the_Bulge/chapter1/chapter1-content.md",
    ]
    meta_body = s3.put_object.call_args_list[0].kwargs["Body"].decode()
    assert "source_video_key:" in meta_body  # provenance for re-processing
    assert "speaker_id_version:" in meta_body
    assert 'speakers_needing_review: "1"' in meta_body
    # source retained: we never delete the S3 object (only local temp files)
    s3.delete_object.assert_not_called()


def test_no_audio_returns_empty():
    s3 = MagicMock()
    with (
        patch.object(pv.boto3, "client", return_value=s3),
        patch.object(pv, "_extract_audio", return_value=False),
    ):
        assert pv.process_video("x/BulgeVideo/v.mp4") == ""
    s3.put_object.assert_not_called()


def test_no_segments_returns_empty():
    s3 = MagicMock()
    with (
        patch.object(pv.boto3, "client", return_value=s3),
        patch.object(pv, "_extract_audio", return_value=True),
        patch("src.ingestion.web_video.GrokTranscriber") as MockT,
    ):
        MockT.return_value.transcribe.return_value = []
        assert pv.process_video("x/BulgeVideo/v.mp4") == ""


def test_failure_alerts_and_retains_source():
    """Any processing failure alerts (never silent) + keeps the source video."""
    s3 = MagicMock()
    with (
        patch.object(pv.boto3, "client", return_value=s3),
        patch.object(pv, "_extract_audio", side_effect=RuntimeError("ffmpeg boom")),
        patch.object(pv, "_alert") as alert,
    ):
        assert pv.process_video("x/BulgeVideo/v.mp4") == ""
    alert.assert_called_once()
    assert alert.call_args.args[0] == "video-processing-failed"
    s3.delete_object.assert_not_called()


def test_main_requires_video_key(monkeypatch):
    monkeypatch.delenv("VIDEO_KEY", raising=False)
    assert pv.main() == 2


def test_apply_resolved_speakers_relabels_with_name_and_role():
    segs = _segs()
    pv._apply_resolved_speakers(
        segs,
        [{"timecode": "00:00:10-00:00:15", "name": "Bradley", "role": "narrator"}],
    )
    assert segs[0].speaker == "Bradley (narrator)"
    assert segs[1].speaker == "2"  # unresolved -> unchanged


# --- video_vision (Grok vision analyzer) ---


def test_vision_analyzer_unknown_on_error():
    from src.ingestion.video_vision import GrokVisionAnalyzer

    client = MagicMock()
    client.extract_json_with_image_base64.side_effect = RuntimeError("api down")
    va = GrokVisionAnalyzer(grok_client=client)
    assert va.analyze_frame(b"\xff\xd8jpeg", [], "ctx") == {}  # unknown, not crash


def test_vision_analyzer_returns_signals():
    from src.ingestion.video_vision import GrokVisionAnalyzer

    client = MagicMock()
    client.extract_json_with_image_base64.return_value = {
        "caption_text": "Gen. Bradley",
        "caption_person_id": "P_B",
        "caption_confidence": 0.9,
        "on_screen_person": True,
        "headshots": [],
    }
    va = GrokVisionAnalyzer(grok_client=client)
    r = va.analyze_frame(b"\xff\xd8jpeg", [], "ctx")
    assert r["caption_text"] == "Gen. Bradley" and r["on_screen_person"] is True
