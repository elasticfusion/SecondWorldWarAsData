"""Tests for video perception (9b) + the 9b->9a end-to-end identify_speakers.

FrameSampler (ffmpeg) and VisionAnalyzer (Grok vision) are mocked — this proves
the wiring + signal shaping without any binary/network. The reliability policy
itself is tested in test_speaker_id.py (9a)."""

from src.ingestion.speaker_id import RosterCandidate, RosterSource
from src.ingestion.video_perception import (
    identify_speakers,
    perceive_segment,
)


class _Sampler:
    def __init__(self, frame=b"\xff\xd8jpeg"):
        self._frame = frame

    def sample(self, video_path, mid_seconds):
        return self._frame


class _NoFrameSampler:
    def sample(self, video_path, mid_seconds):
        return None


class _Vision:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def analyze_frame(self, frame, roster, transcript_context):
        self.calls += 1
        return self._result


_ROSTER = [
    RosterCandidate("P_BRADLEY", "Omar N. Bradley", RosterSource.IMDB_CAST),
    RosterCandidate("P_PATTON", "George S. Patton", RosterSource.TRANSCRIPT),
]


def _seg(**kw):
    base = dict(
        speaker_label="Speaker 1",
        timecode="00:00:10-00:00:20",
        start=10.0,
        end=20.0,
        transcript_context="the general said",
    )
    base.update(kw)
    return base


def test_perceive_reads_caption_signal():
    vision = _Vision(
        {
            "caption_text": "Gen. Bradley",
            "caption_person_id": "P_BRADLEY",
            "caption_confidence": 0.9,
        }
    )
    sig = perceive_segment(
        video_path="v.mkv", **_seg(), roster=_ROSTER, sampler=_Sampler(), vision=vision
    )
    assert sig.caption is not None
    assert sig.caption.person_id == "P_BRADLEY"
    assert sig.caption.confidence == 0.9
    assert vision.calls == 1


def test_perceive_reads_headshot_signals():
    vision = _Vision(
        {
            "headshots": [
                {"person_id": "P_PATTON", "name": "George S. Patton", "confidence": 0.7}
            ]
        }
    )
    sig = perceive_segment(
        video_path="v.mkv", **_seg(), roster=_ROSTER, sampler=_Sampler(), vision=vision
    )
    assert sig.caption is None
    assert len(sig.headshots) == 1 and sig.headshots[0].person_id == "P_PATTON"


def test_missing_frame_yields_empty_signals_no_vision_call():
    vision = _Vision({"caption_text": "should not be used"})
    sig = perceive_segment(
        video_path="v.mkv",
        **_seg(),
        roster=_ROSTER,
        sampler=_NoFrameSampler(),
        vision=vision,
    )
    assert sig.caption is None and not sig.headshots
    assert vision.calls == 0  # no frame -> vision not called


def test_vision_unknown_yields_empty_signals():
    sig = perceive_segment(
        video_path="v.mkv",
        **_seg(),
        roster=_ROSTER,
        sampler=_Sampler(),
        vision=_Vision({"caption_text": "", "headshots": []}),
    )
    assert sig.caption is None and not sig.headshots


def test_identify_speakers_end_to_end_caption_asserts_headshot_gated():
    """9b->9a: a caption segment auto-asserts; a headshot-only segment is
    review-gated — proving perception feeds the resolver's policy correctly."""
    segments = [
        _seg(speaker_label="Speaker 1", timecode="00:00:10-00:00:20"),
        _seg(
            speaker_label="Speaker 2",
            timecode="00:00:30-00:00:40",
            start=30.0,
            end=40.0,
        ),
    ]

    class _PerSegVision:
        def analyze_frame(self, frame, roster, transcript_context):
            # deterministic per-call: first caption, then headshot-only
            if not hasattr(self, "_n"):
                self._n = 0
            self._n += 1
            if self._n == 1:
                return {
                    "caption_text": "Gen. Bradley",
                    "caption_person_id": "P_BRADLEY",
                    "caption_confidence": 0.95,
                }
            return {
                "headshots": [
                    {
                        "person_id": "P_PATTON",
                        "name": "George S. Patton",
                        "confidence": 0.9,
                    }
                ]
            }

    resolved = identify_speakers(
        video_path="v.mkv",
        segments=segments,
        roster=_ROSTER,
        sampler=_Sampler(),
        vision=_PerSegVision(),
    )
    cap, head = resolved
    assert cap["person_id"] == "P_BRADLEY" and cap["needs_review"] is False
    assert head["person_id"] == "P_PATTON" and head["needs_review"] is True
