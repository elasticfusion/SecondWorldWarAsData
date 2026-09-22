"""Tests for the web-page-with-video handler (requirement #7).

Covers the provenance model and page<->video binding, the pluggable transcriber
interface, the flag-don't-fabricate default (NullTranscriber), and the
GrokTranscriber backend (xAI /v1/stt) with mocked HTTP.
"""

from typing import List
from unittest.mock import MagicMock

import pytest

from src.ingestion.web_video import (
    GrokTranscriber,
    NullTranscriber,
    Transcriber,
    TranscriptSegment,
    VideoAsset,
    capture_web_page_with_video,
    render_transcript_markdown,
    write_transcript_markdown,
)


class _FakeTranscriber:  # pylint: disable=too-few-public-methods
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


# --- GrokTranscriber (xAI /v1/stt), with mocked HTTP ------------------------

# A representative /v1/stt response: word-level timestamps, two clean sentences
# (no mid-sentence abbreviations, so grouping is unambiguous).
_STT_RESPONSE = {
    "text": "We will hold the town. The situation is grave.",
    "language": "en",
    "duration": 4.0,
    "words": [
        {"text": "We", "start": 0.0, "end": 0.3},
        {"text": "will", "start": 0.3, "end": 0.6},
        {"text": "hold", "start": 0.6, "end": 0.9},
        {"text": "the", "start": 0.9, "end": 1.1},
        {"text": "town.", "start": 1.1, "end": 1.5},
        {"text": "The", "start": 2.0, "end": 2.2},
        {"text": "situation", "start": 2.2, "end": 2.8},
        {"text": "is", "start": 2.8, "end": 3.0},
        {"text": "grave.", "start": 3.0, "end": 3.6},
    ],
}


def _mock_post(monkeypatch, payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    post = MagicMock(return_value=resp)
    monkeypatch.setattr("src.ingestion.web_video.requests.post", post)
    return post


def test_grok_transcriber_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    with pytest.raises(ValueError):
        GrokTranscriber()


def test_grok_transcriber_groups_words_into_sentence_segments(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    post = _mock_post(monkeypatch, _STT_RESPONSE)
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3fakeaudio")

    segs = GrokTranscriber(language="en").transcribe(str(clip))

    # Grouped at sentence-ending punctuation -> two segments, not nine words.
    assert len(segs) == 2
    assert segs[0].text == "We will hold the town."
    assert segs[0].start == 0.0 and segs[0].end == 1.5
    assert segs[0].timecode == "00:00:00-00:00:01"
    assert segs[1].text == "The situation is grave."
    # Called the /v1/stt endpoint with bearer auth and file last.
    args, kwargs = post.call_args
    assert args[0].endswith("/v1/stt")
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert "file" in kwargs["files"]


def test_grok_transcriber_conforms_to_protocol(monkeypatch) -> None:
    monkeypatch.setenv("GROK_API_KEY", "k")
    assert isinstance(GrokTranscriber(), Transcriber)


def test_capture_uses_grok_transcriber_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    _mock_post(monkeypatch, _STT_RESPONSE)
    clip = tmp_path / "interview.mp3"
    clip.write_bytes(b"ID3fakeaudio")

    cap = capture_web_page_with_video(
        page_id="01PAGE",
        asset_id="01VID",
        url="https://example.org/interview",
        page_text="An interview.",
        video_local_path=str(clip),
        transcriber=GrokTranscriber(language="en"),
    )
    assert cap.video.transcription_status == "complete"
    assert cap.video.needs_review is False
    assert "hold the town" in cap.video.transcript_text()
    seg = cap.video.segment_for_time(3.1)
    assert seg is not None and "grave" in seg.text


def test_grok_transcriber_skips_malformed_words(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_API_KEY", "k")
    payload = {"words": [{"text": "Hi.", "start": 0.0, "end": 0.5}, {"bad": "x"}]}
    _mock_post(monkeypatch, payload)
    clip = tmp_path / "c.wav"
    clip.write_bytes(b"RIFF")
    segs = GrokTranscriber().transcribe(str(clip))
    assert len(segs) == 1 and segs[0].text == "Hi."


# --- transcript -> timecoded markdown rendering -----------------------------


def _sample_video() -> VideoAsset:
    return VideoAsset(
        asset_id="01VIDEO",
        source_url="https://example.org/doc.mp4",
        segments=[
            TranscriptSegment(0.0, 11.0, "Three German armies launch a counterattack."),
            TranscriptSegment(12.0, 14.0, "Their goal: turn the tide.", speaker="0"),
        ],
        transcription_status="complete",
        needs_review=False,
    )


def test_render_transcript_preserves_timecodes_and_metadata() -> None:
    md = render_transcript_markdown(_sample_video(), title="Doc")
    assert md.startswith("# Doc")
    assert "asset_id: 01VIDEO" in md
    assert "transcription_status: complete" in md
    # Each segment carries its timecode as a bold prefix (provenance anchor).
    assert "**[00:00:00-00:00:11]**" in md
    assert "**[00:00:12-00:00:14]** (Speaker 0)" in md
    assert "counterattack." in md and "turn the tide." in md


def test_render_transcript_flows_through_parser_as_prose() -> None:
    from src.parser import split_into_blocks

    md = render_transcript_markdown(_sample_video(), title="Doc")
    paras = [t for t, _ in split_into_blocks(md)]
    # Transcript text is extractable prose, with timecodes surviving into it.
    assert any("counterattack." in p for p in paras)
    assert any("00:00:12-00:00:14" in p for p in paras)


def test_render_transcript_pending_is_not_fabricated() -> None:
    md = render_transcript_markdown(
        VideoAsset(asset_id="01X", transcription_status="pending")
    )
    assert "No transcript available" in md
    assert "pending" in md
    assert "needs_review: true" in md  # pending asset defaults needs_review=True


def test_write_transcript_markdown_writes_file(tmp_path) -> None:
    out = tmp_path / "sub" / "transcript.md"
    written = write_transcript_markdown(_sample_video(), out, title="Doc")
    assert written == out and out.exists()
    assert "**[00:00:00-00:00:11]**" in out.read_text(encoding="utf-8")
