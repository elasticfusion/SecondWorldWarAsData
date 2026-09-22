"""Web-page-with-video handler: page capture + timecoded video transcript.

Requirement #7 (see docs/current/dataquality/STRUCTURED_DATA_ROUTING.md,
"Web page containing video"): a web page that embeds a video yields *two* linked
provenance objects, both retained, so any asserted fact traces back to source:

1. **The web page** — captured as a source/media record with its URL and
   ``capture_date`` (web pages are mutable; the fetch timestamp makes a
   web-sourced fact reproducible). The page text is retained for later
   summarization; a summary entity cites the captured page, so it never floats
   free of its source.
2. **The embedded video** — registered as a media asset whose transcript is
   split into **timecoded segments**. A specific spoken assertion (a general's
   or politician's statement) therefore binds to ``(asset_id, start-end)``.

The provenance anchors mirror print's ``verbatim_reference`` + page number:
``(url, capture_date)`` for the page and ``(asset_id, timecode)`` for the video.

**Scope of this module.** The *model and binding* plus a real transcription
backend are provided. :class:`GrokTranscriber` calls the xAI Grok
Speech-to-Text API (``/v1/stt``) and maps its word-level timestamps into
:class:`TranscriptSegment` spans. :class:`NullTranscriber` remains the default,
recording the video as an asset flagged ``needs_review`` (transcription pending)
rather than fabricating a transcript when no backend is supplied — consistent
with the project's "flag, never fabricate" discipline.

This module does not itself fetch the network or decode video; callers supply
the already-fetched page text and a local video path (obtained by the
acquisition layer), keeping this unit testable and side-effect free.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable

import requests

from src.ingestion.source_metadata import capture_now_iso


@dataclass
class TranscriptSegment:
    """One timecoded span of a video transcript.

    Attributes:
        start: Segment start time in seconds from the video's beginning.
        end: Segment end time in seconds.
        text: The spoken text in this segment.
        speaker: Optional speaker label when diarization is available.
    """

    start: float
    end: float
    text: str
    speaker: Optional[str] = None

    @property
    def timecode(self) -> str:
        """Return the segment's provenance anchor as ``HH:MM:SS-HH:MM:SS``."""
        return f"{_fmt_ts(self.start)}-{_fmt_ts(self.end)}"


def _fmt_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS (floored)."""
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


@runtime_checkable
class Transcriber(Protocol):  # pylint: disable=too-few-public-methods
    """Pluggable transcription backend.

    Implementations turn a local audio/video file into timecoded segments. The
    whisper backend implements this later; the model/binding here depends only
    on this interface, not on any engine.
    """

    def transcribe(self, media_path: str) -> List[TranscriptSegment]:
        """Return timecoded transcript segments for the media at ``media_path``."""


class NullTranscriber:  # pylint: disable=too-few-public-methods
    """Default transcriber that produces no segments (transcription pending).

    Records the video as a first-class asset without fabricating a transcript.
    The resulting :class:`VideoAsset` is flagged ``needs_review`` so a real
    transcriber (or a human) completes it later. This lets the page<->video
    binding and provenance model be exercised end-to-end now.
    """

    def transcribe(
        self, media_path: str  # pylint: disable=unused-argument
    ) -> List[TranscriptSegment]:
        """Return no segments: a transcript is never invented (flag, don't fabricate)."""
        return []


# Group word-level timestamps into segments at these sentence-ending marks.
_SEGMENT_END_CHARS = (".", "!", "?")
# Cap segment length so a run without punctuation still yields usable spans.
_MAX_WORDS_PER_SEGMENT = 40


class GrokTranscriber:  # pylint: disable=too-few-public-methods
    """Transcriber backed by the xAI Grok Speech-to-Text API (``/v1/stt``).

    Calls the batch REST endpoint (``POST https://api.x.ai/v1/stt``,
    multipart/form-data) and groups the API's *word-level* timestamps into
    sentence-ish :class:`TranscriptSegment` spans (the API returns per-word
    timings, not sentence segments). Reuses the project's ``GROK_API_KEY``
    convention; the base URL is overridable via ``GROK_STT_URL`` for testing.

    Grouping policy: a segment ends at sentence-ending punctuation, on a speaker
    change (when ``diarize`` is on), or at ``_MAX_WORDS_PER_SEGMENT`` — so a long
    unpunctuated stretch still produces bounded, timecoded spans.

    Network is only touched on ``transcribe``; construction is side-effect free,
    so the binding/model can be unit-tested with a mocked HTTP response.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "grok-voice-transcribe-2.0",
        language: Optional[str] = None,
        diarize: bool = False,
        timeout: float = 600.0,
    ) -> None:
        self.api_key = api_key or os.getenv("GROK_API_KEY") or ""
        if not self.api_key:
            raise ValueError("GROK_API_KEY not found for GrokTranscriber")
        self.model = model
        self.language = language
        self.diarize = diarize
        self.timeout = timeout
        self.url = os.getenv("GROK_STT_URL", "https://api.x.ai/v1/stt")

    def transcribe(self, media_path: str) -> List[TranscriptSegment]:
        """Transcribe a local audio/video file into timecoded segments."""
        payload = self._request(media_path)
        return self._segments_from_words(payload.get("words", []))

    def _request(self, media_path: str) -> dict:
        """POST the file to /v1/stt and return the parsed JSON response.

        ``file`` is sent last, as the API requires (fields after ``file`` may be
        ignored for streamable uploads).
        """
        data = [("model", self.model)]
        if self.language:
            data.append(("language", self.language))
        if self.diarize:
            data.append(("diarize", "true"))
        with open(media_path, "rb") as handle:
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"file": (os.path.basename(media_path), handle)},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _segments_from_words(words: List[dict]) -> List[TranscriptSegment]:
        """Group per-word timestamps into sentence-ish timecoded segments.

        Boundaries: sentence-ending punctuation, a speaker change, or the
        word cap. Empty/malformed words are skipped defensively.
        """
        segments: List[TranscriptSegment] = []
        buf: List[dict] = []

        def flush() -> None:
            if not buf:
                return
            text = " ".join(w["text"] for w in buf).strip()
            if text:
                speaker = buf[0].get("speaker")
                segments.append(
                    TranscriptSegment(
                        start=float(buf[0]["start"]),
                        end=float(buf[-1]["end"]),
                        text=text,
                        speaker=(str(speaker) if speaker is not None else None),
                    )
                )
            buf.clear()

        prev_speaker = None
        for word in words:
            if "text" not in word or "start" not in word or "end" not in word:
                continue
            speaker = word.get("speaker")
            if buf and speaker != prev_speaker and speaker is not None:
                flush()
            buf.append(word)
            prev_speaker = speaker
            ends_sentence = word["text"].rstrip().endswith(_SEGMENT_END_CHARS)
            if ends_sentence or len(buf) >= _MAX_WORDS_PER_SEGMENT:
                flush()
        flush()
        return segments


@dataclass
class VideoAsset:  # pylint: disable=too-many-instance-attributes
    """A video registered as a media asset with a timecoded transcript.

    Attributes:
        asset_id: Stable identifier (ULID recommended), assigned by the caller.
        source_url: URL the video was embedded at / obtained from, if any.
        local_path: Local path to the obtained video file, if any.
        media_type: Always "video" for this asset.
        segments: Timecoded transcript segments (empty when pending).
        transcription_status: "complete" when segments were produced,
            "pending" when the NullTranscriber left it for later.
        needs_review: True while transcription is pending.
        notes: Free text (e.g. why review is flagged).
    """

    asset_id: str
    source_url: Optional[str] = None
    local_path: Optional[str] = None
    media_type: str = "video"
    segments: List[TranscriptSegment] = field(default_factory=list)
    transcription_status: str = "pending"
    needs_review: bool = True
    notes: str = ""

    def transcript_text(self) -> str:
        """Return the full transcript as prose (segments joined in order).

        This prose flows through the normal entity pipeline; each extracted
        claim can still cite its segment via :meth:`segment_for_offset`.
        """
        return " ".join(seg.text.strip() for seg in self.segments if seg.text.strip())

    def segment_for_time(self, seconds: float) -> Optional[TranscriptSegment]:
        """Return the segment covering ``seconds``, if any (provenance lookup)."""
        for seg in self.segments:
            if seg.start <= seconds <= seg.end:
                return seg
        return None


@dataclass
class WebPageRecord:
    """A captured web page (source of truth for web-asserted facts).

    Attributes:
        page_id: Stable identifier (ULID recommended), assigned by the caller.
        url: The page URL.
        capture_date: ISO-8601 UTC fetch/snapshot timestamp (required for
            reproducibility of web sources).
        text: The page's extracted text, retained for later summarization.
        title: Optional page title.
    """

    page_id: str
    url: str
    capture_date: str
    text: str = ""
    title: Optional[str] = None


@dataclass
class WebVideoCapture:
    """The composite result: a page and its embedded video, cross-linked.

    The page and the video reference each other (and, downstream, the same
    events/people), so a fact surfaced from the video resolves to *both* the
    page it appeared on and the exact moment in the recording.

    Attributes:
        page: The captured web-page record.
        video: The registered video asset (may be transcription-pending).
        needs_review: True when any component needs human confirmation
            (currently: transcription pending).
    """

    page: WebPageRecord
    video: VideoAsset
    needs_review: bool = True


def capture_web_page_with_video(
    *,
    page_id: str,
    asset_id: str,
    url: str,
    page_text: str,
    video_local_path: Optional[str] = None,
    video_source_url: Optional[str] = None,
    title: Optional[str] = None,
    capture_date: Optional[str] = None,
    transcriber: Optional[Transcriber] = None,
) -> WebVideoCapture:
    """Build a cross-linked page + video capture.

    Caller supplies the already-fetched page text and (optionally) a local video
    path; this function does no network or video decoding of its own. The video
    is transcribed via ``transcriber`` (defaults to :class:`NullTranscriber`, so
    transcription is recorded as pending rather than fabricated).

    Args:
        page_id: Identifier for the page record.
        asset_id: Identifier for the video asset.
        url: The web page URL.
        page_text: Already-extracted page text (retained for summarization).
        video_local_path: Local path to the obtained video, if available.
        video_source_url: URL the video was embedded at / sourced from.
        title: Optional page title.
        capture_date: ISO-8601 UTC capture timestamp; defaults to now.
        transcriber: Transcription backend; defaults to NullTranscriber.

    Returns:
        A :class:`WebVideoCapture` with the page and video cross-linked and a
        ``needs_review`` flag reflecting transcription status.
    """
    engine: Transcriber = transcriber or NullTranscriber()
    capture_ts = capture_date or capture_now_iso()

    page = WebPageRecord(
        page_id=page_id,
        url=url,
        capture_date=capture_ts,
        text=page_text,
        title=title,
    )

    segments: List[TranscriptSegment] = []
    if video_local_path is not None:
        segments = list(engine.transcribe(video_local_path))

    complete = bool(segments)
    video = VideoAsset(
        asset_id=asset_id,
        source_url=video_source_url or url,
        local_path=video_local_path,
        segments=segments,
        transcription_status="complete" if complete else "pending",
        needs_review=not complete,
        notes="" if complete else "transcription pending (no transcriber run)",
    )

    return WebVideoCapture(page=page, video=video, needs_review=video.needs_review)


def render_transcript_markdown(
    video: VideoAsset,
    *,
    title: Optional[str] = None,
    source_url: Optional[str] = None,
) -> str:
    """Render a video's timecoded transcript to a markdown document.

    Each transcript segment becomes a block prefixed with its timecode, so the
    ``(asset_id, timecode)`` provenance survives into the markdown and downstream
    parse. The result is ordinary prose that flows through the existing pipeline
    (Phase 1 parse -> Phase 2 extraction) like any other text source, and is a
    natural chunk source for RAG.

    Format per segment:

        **[HH:MM:SS-HH:MM:SS]** (Speaker N) segment text

    A leading metadata block records the asset id, source, and transcription
    status so a fact extracted from the transcript traces back to the recording.
    Returns an empty-transcript notice (not fabricated text) when the video has
    no segments (e.g. transcription still pending).
    """
    lines: List[str] = []
    heading = title or "Video Transcript"
    lines.append(f"# {heading}")
    lines.append("")
    lines.append(f"- asset_id: {video.asset_id}")
    if source_url or video.source_url:
        lines.append(f"- source: {source_url or video.source_url}")
    lines.append(f"- transcription_status: {video.transcription_status}")
    if video.needs_review:
        lines.append("- needs_review: true")
    lines.append("")

    if not video.segments:
        lines.append("_No transcript available (transcription pending)._")
        return "\n".join(lines) + "\n"

    for seg in video.segments:
        speaker = f" (Speaker {seg.speaker})" if seg.speaker else ""
        text = seg.text.strip()
        if not text:
            continue
        lines.append(f"**[{seg.timecode}]**{speaker} {text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_transcript_markdown(
    video: VideoAsset,
    out_path: Path,
    *,
    title: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Path:
    """Render the transcript and write it to ``out_path`` (parent dirs created).

    Returns the written path. The document is the pipeline's markdown contract,
    so it can be dropped where content discovery finds it.
    """
    markdown = render_transcript_markdown(video, title=title, source_url=source_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    return out_path
