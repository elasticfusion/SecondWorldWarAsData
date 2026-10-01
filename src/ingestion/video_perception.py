"""Video perception for speaker identification (9b).

Turns a video + its diarized transcript into the per-timecode perception signals
that the deterministic resolver (9a, ``speaker_id.py``) fuses into identities.

For each speaker segment it samples a representative frame (via a ``FrameSampler``
— ffmpeg in production, injectable for tests) and asks a vision model (Grok, via
an injectable ``VisionAnalyzer``) two things, framed as a SUGGESTION with an
explicit "unknown" escape hatch (never a closed set):

1. **Read any on-screen name caption/chyron** (the PRIMARY signal).
2. **Which — if any — of these roster candidates is the on-screen person**,
   given their reference headshots (the FALLBACK signal).

The roster is passed as a *prior* (candidates + reference images + names); the
vision model may answer "unknown". This module does NO fusion/decision-making —
it only produces signals; the resolver owns the reliability policy. It lives in
the separate video container (ffmpeg + vision), not the shared pipeline image.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from src.ingestion.speaker_id import (
    CaptionSignal,
    HeadshotSignal,
    RosterCandidate,
    SpeakerSignals,
)


@runtime_checkable
class FrameSampler(Protocol):
    """Extracts a representative frame for a timecode. ffmpeg in production."""

    def sample(self, video_path: str, mid_seconds: float) -> Optional[bytes]:
        """Return frame image bytes near mid_seconds, or None if unavailable."""


@runtime_checkable
class VisionAnalyzer(Protocol):
    """Vision perception over one frame + a roster prior. Grok in production."""

    def analyze_frame(
        self, frame: bytes, roster: List[RosterCandidate], transcript_context: str
    ) -> dict:
        """Return a dict with optional keys:
        caption_text: str | None       — on-screen name read from a chyron
        caption_person_id: str | None  — resolved to a roster id if confident
        caption_confidence: float
        headshots: [ {person_id, name, confidence} ]  — ranked roster matches
        (may be empty / 'unknown')."""


def _mid_seconds(start: float, end: float) -> float:
    """A representative sampling point — a little past the segment start, so we
    catch the speaker on-screen rather than a transition frame."""
    return start + min(1.5, (end - start) / 2.0)


def perceive_segment(
    *,
    video_path: str,
    speaker_label: str,
    timecode: str,
    start: float,
    end: float,
    transcript_context: str,
    roster: List[RosterCandidate],
    sampler: FrameSampler,
    vision: VisionAnalyzer,
) -> SpeakerSignals:
    """Produce the perception signals for one speaker segment.

    Returns SpeakerSignals with whatever the frame yielded; a missing frame or a
    vision 'unknown' simply yields empty signals (the resolver then treats the
    segment as unknown -> needs-review). Never fabricates."""
    signals = SpeakerSignals(
        speaker_label=speaker_label,
        timecode=timecode,
        transcript_context=transcript_context,
    )
    frame = sampler.sample(video_path, _mid_seconds(start, end))
    if frame is None:
        return signals  # no frame -> no visual signal (resolver: unknown/review)

    result = vision.analyze_frame(frame, roster, transcript_context) or {}

    # Role signals: did the frame show a talking person on camera (-> interviewee)
    # or is this voice-over footage (-> narrator)? Perception may also hint a role.
    if "on_screen_person" in result:
        signals.on_screen_person = bool(result.get("on_screen_person"))
    role_hint = result.get("role_hint")
    if role_hint:
        from src.ingestion.speaker_id import SpeakerRole

        try:
            signals.role_hint = SpeakerRole(role_hint)
        except ValueError:
            signals.role_hint = None

    cap_text = (result.get("caption_text") or "").strip()
    if cap_text:
        signals.caption = CaptionSignal(
            text=cap_text,
            person_id=result.get("caption_person_id"),
            confidence=float(result.get("caption_confidence", 0.0) or 0.0),
        )
    for h in result.get("headshots", []) or []:
        pid = h.get("person_id")
        if not pid:
            continue
        signals.headshots.append(
            HeadshotSignal(
                person_id=pid,
                name=h.get("name", ""),
                confidence=float(h.get("confidence", 0.0) or 0.0),
            )
        )
    return signals


def perceive_video(
    *,
    video_path: str,
    segments: List[dict],
    roster: List[RosterCandidate],
    sampler: FrameSampler,
    vision: VisionAnalyzer,
) -> List[SpeakerSignals]:
    """Produce perception signals for every diarized segment.

    segments: list of {speaker_label, timecode, start, end, transcript_context}
    (from the transcript). Returns SpeakerSignals per segment, ready for
    speaker_id.resolve_speakers()."""
    out: List[SpeakerSignals] = []
    for seg in segments:
        out.append(
            perceive_segment(
                video_path=video_path,
                speaker_label=seg["speaker_label"],
                timecode=seg["timecode"],
                start=float(seg["start"]),
                end=float(seg["end"]),
                transcript_context=seg.get("transcript_context", ""),
                roster=roster,
                sampler=sampler,
                vision=vision,
            )
        )
    return out


def identify_speakers(
    *,
    video_path: str,
    segments: List[dict],
    roster: List[RosterCandidate],
    sampler: FrameSampler,
    vision: VisionAnalyzer,
) -> List[dict]:
    """End-to-end 9b->9a: perceive each segment's frame, then run the
    deterministic resolver. Returns the resolved-speaker dicts (identity +
    confidence + provenance + needs_review) ready to render into the transcript
    markdown / attach to People mentions."""
    from src.ingestion.speaker_id import resolve_speakers

    signals = perceive_video(
        video_path=video_path,
        segments=segments,
        roster=roster,
        sampler=sampler,
        vision=vision,
    )
    return [r.to_dict() for r in resolve_speakers(signals, roster)]
