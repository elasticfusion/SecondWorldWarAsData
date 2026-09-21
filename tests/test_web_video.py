"""Tests for the web-page-with-video handler (requirement #7 scaffold).

Covers the provenance model and page<->video binding, the pluggable transcriber
interface, and the flag-don't-fabricate default (NullTranscriber leaves
transcription pending rather than inventing a transcript).
"""

from typing import List

from src.ingestion.web_video import (
    NullTranscriber,
    TranscriptSegment,
    Transcriber,
    VideoAsset,
    capture_web_page_with_video,
)


class _FakeTranscriber:
    """A stand-in transcriber returning fixed segments (mimics a real backend)."""

    def transcribe(self, media_path: str) -> List[TranscriptSegment]:
        assert media_path  # path is passed through
        return [
            TranscriptSegment(0.0, 5.0, "We will hold St. Vith.", speaker="Clarke"),
            TranscriptSegment(5.0, 9.0, "The situation is grave."),
        ]


def test_null_transcriber_leaves_pending_not_fabricated() -> None:
    cap = capture_web_page_with_video(
        page_id="01PAGE",
        asset_id="01VID",
        url="https://example.org/interview",
        page_text="An interview about the Battle of St. Vith.",
        video_local_path="/tmp/clip.mp4",
    )
    # No transcript invented.
    assert cap.video.segments == []
    assert cap.video.transcription_status == "pending"
    assert cap.video.needs_review is True
    assert cap.needs_review is True


def test_page_capture_records_url_and_capture_date() -> None:
    cap = capture_web_page_with_video(
        page_id="01PAGE",
        asset_id="01VID",
        url="https://example.org/interview",
        page_text="Page body retained for later summarization.",
        title="Interview",
        capture_date="2026-09-20T00:00:00+00:00",
    )
    assert cap.page.url == "https://example.org/interview"
    assert cap.page.capture_date == "2026-09-20T00:00:00+00:00"
    assert "summarization" in cap.page.text
    assert cap.page.title == "Interview"


def test_capture_date_defaults_to_now() -> None:
    cap = capture_web_page_with_video(
        page_id="p", asset_id="v", url="https://x.test", page_text=""
    )
    # ISO-8601 UTC timestamp assigned automatically.
    assert cap.page.capture_date.endswith("+00:00")


def test_real_transcriber_produces_timecoded_segments() -> None:
    cap = capture_web_page_with_video(
        page_id="01PAGE",
        asset_id="01VID",
        url="https://example.org/interview",
        page_text="",
        video_local_path="/tmp/clip.mp4",
        video_source_url="https://cdn.example.org/clip.mp4",
        transcriber=_FakeTranscriber(),
    )
    assert cap.video.transcription_status == "complete"
    assert cap.video.needs_review is False
    assert cap.needs_review is False
    # Timecode provenance anchor.
    assert cap.video.segments[0].timecode == "00:00:00-00:00:05"
    # Full transcript is prose for the entity pipeline.
    assert "hold St. Vith" in cap.video.transcript_text()
    # A spoken assertion binds to its segment via time lookup.
    seg = cap.video.segment_for_time(6.0)
    assert seg is not None and "grave" in seg.text
    # Video is cross-linked to its source.
    assert cap.video.source_url == "https://cdn.example.org/clip.mp4"


def test_segment_timecode_formatting() -> None:
    seg = TranscriptSegment(3661.0, 3725.0, "x")  # 1h01m01s - 1h02m05s
    assert seg.timecode == "01:01:01-01:02:05"


def test_null_transcriber_satisfies_protocol() -> None:
    # The default engine conforms to the Transcriber protocol.
    assert isinstance(NullTranscriber(), Transcriber)
    assert isinstance(_FakeTranscriber(), Transcriber)


def test_no_video_path_means_no_segments() -> None:
    cap = capture_web_page_with_video(
        page_id="p",
        asset_id="v",
        url="https://x.test",
        page_text="text only",
        transcriber=_FakeTranscriber(),  # not called without a path
    )
    assert cap.video.segments == []
    assert cap.video.transcription_status == "pending"
