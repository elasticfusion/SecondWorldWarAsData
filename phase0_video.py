#!/usr/bin/env python3
"""Phase 0 (video): transcribe a video + identify speakers -> chapter-structure
markdown, so video joins the same parse -> extract -> enrich lifecycle as every
other source.

Runs in the SEPARATE video container (Dockerfile.video: ffmpeg + these modules),
demand-launched by the trigger with the target key in VIDEO_KEY (never a standing
service). Pipeline:

  1. download VIDEO_KEY from S3 (the SOURCE OF TRUTH — retained for re-processing)
  2. ffmpeg: extract audio (for STT) + sample frames at speaker boundaries
  3. GrokTranscriber (STT, diarized) -> timecoded TranscriptSegments
  4. build the tiered candidate roster (video_roster / 9c)
  5. perceive each segment's frame (video_perception / 9b: caption + headshot)
  6. resolve speakers deterministically (speaker_id / 9a: caption-primary,
     headshot review-gated, narrator role, cross-signal agreement)
  7. render transcript markdown with RESOLVED names + roles + provenance
     (source_video_key + speaker_id_version) -> contentrepository/{book}/chapter1/
     chapter1-{meta.yaml,content.md} (mirrors ocr_merge / phase0_convert) -> parse

The heavy externals (ffmpeg, Grok STT/vision, roster HTTP) are injected/guarded
so the orchestration is unit-testable without binaries or network.
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - trusted, code-constructed ffmpeg command
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

import boto3

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("phase0_video")

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BUCKET = os.getenv("S3_BUCKET", "")
SPEAKER_ID_VERSION = "1"  # bump when the vision/roster/resolver logic changes
_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")


def _book_from_key(key: str) -> str:
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.replace(" ", "_")


def _load_grok_key() -> None:
    """Load GROK_API_KEY into the env from Secrets Manager (SECRETS_ID) so
    GrokTranscriber + GrokVisionAnalyzer (which read GROK_API_KEY) work in the
    container. No-op if the key is already set or no SECRETS_ID is configured."""
    if os.getenv("GROK_API_KEY"):
        return
    secret_id = os.getenv("SECRETS_ID", "")
    if not secret_id:
        logger.warning("No SECRETS_ID/GROK_API_KEY — transcription will fail")
        return
    try:
        sm = boto3.client("secretsmanager", region_name=REGION)
        os.environ["GROK_API_KEY"] = sm.get_secret_value(SecretId=secret_id)[
            "SecretString"
        ]
        logger.info("Loaded GROK_API_KEY from Secrets Manager")
    except Exception as e:  # pragma: no cover
        logger.error("Failed to load GROK_API_KEY from %s: %s", secret_id, e)


def _extract_audio(video_path: str, out_wav: str) -> bool:
    """ffmpeg: extract mono 16kHz audio for STT. Returns True on success."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        out_wav,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=3600)  # nosec B603
        return os.path.exists(out_wav) and os.path.getsize(out_wav) > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error("ffmpeg audio extraction failed for %s: %s", video_path, e)
        return False


def _frame_sampler(video_path: str):
    """Return a FrameSampler (video_perception protocol) backed by ffmpeg — grabs
    one JPEG frame at a given second."""

    class _FF:
        def sample(self, video_path: str, mid_seconds: float) -> Optional[bytes]:
            fd, tmp = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(max(0.0, mid_seconds)),
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                tmp,
            ]
            try:
                subprocess.run(
                    cmd, check=True, capture_output=True, timeout=120
                )  # nosec B603
                with open(tmp, "rb") as fh:
                    return fh.read()
            except Exception as e:  # pragma: no cover - env dependent
                logger.warning("frame sample @%.1fs failed: %s", mid_seconds, e)
                return None
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)

    return _FF()


def _segments_to_perception_inputs(segments) -> List[dict]:
    """Map transcript segments -> the per-segment dicts video_perception wants."""
    out = []
    for seg in segments:
        out.append(
            {
                "speaker_label": seg.speaker or "unknown",
                "timecode": seg.timecode,
                "start": float(seg.start),
                "end": float(seg.end),
                "transcript_context": seg.text[:500],
            }
        )
    return out


def process_video(key: str) -> str:
    """Transcribe + speaker-identify VIDEO_KEY, write the chapter structure.
    Returns the content output key, or "" on skip/failure (logged)."""
    if not BUCKET:
        raise RuntimeError("S3_BUCKET env var not configured")
    ext = Path(key).suffix.lower()
    if ext not in _VIDEO_EXTS:
        logger.warning("Unsupported video extension %s (%s) — skipping", ext, key)
        return ""

    s3 = boto3.client("s3", region_name=REGION)
    book = _book_from_key(key)
    workdir = tempfile.mkdtemp(prefix="video-")
    local = os.path.join(workdir, "input" + ext)
    wav = os.path.join(workdir, "audio.wav")
    try:
        _load_grok_key()  # GrokTranscriber/vision read GROK_API_KEY from env
        # 1) download (source of truth stays in S3; local is a working copy)
        s3.download_file(BUCKET, key, local)

        # 2+3) audio -> transcribe (diarized)
        from src.ingestion.web_video import GrokTranscriber, render_transcript_markdown
        from src.ingestion.web_video import VideoAsset

        if not _extract_audio(local, wav):
            logger.error("No audio extracted for %s — cannot transcribe", key)
            return ""
        transcriber = GrokTranscriber(diarize=True)
        segments = transcriber.transcribe(wav)
        if not segments:
            logger.warning("Transcription produced no segments for %s", key)
            return ""

        # 4) roster (9c) — transcript-derived + optional IMDB/Wikipedia by title
        from src.ingestion.video_roster import build_roster, WikipediaAppearanceLookup

        roster = build_roster(
            title=book.replace("_", " "),
            transcript_people=[],  # populated post-extraction in a later pass
            appearance_lookup=WikipediaAppearanceLookup(),
        )

        # 5+6) perceive frames (9b) -> resolve speakers (9a)
        from src.ingestion.video_perception import identify_speakers

        resolved = identify_speakers(
            video_path=local,
            segments=_segments_to_perception_inputs(segments),
            roster=roster,
            sampler=_frame_sampler(local),
            vision=_vision_analyzer(),
        )
        _apply_resolved_speakers(segments, resolved)

        # 7) render markdown w/ resolved names + roles + provenance -> chapter structure
        asset = VideoAsset(
            asset_id=book,
            source_url=f"s3://{BUCKET}/{key}",
            segments=segments,
            transcription_status="complete",
            needs_review=any(r.get("needs_review") for r in resolved),
        )
        markdown = render_transcript_markdown(
            asset, title=book.replace("_", " "), source_url=f"s3://{BUCKET}/{key}"
        )
        return _write_chapter_structure(s3, key, book, markdown, resolved)
    except Exception as exc:  # noqa: BLE001 - alert on ANY failure (never silent)
        _alert(
            "video-processing-failed",
            f"Video {key} ({book}) failed processing: {exc}. Source retained at "
            f"s3://{BUCKET}/{key} for re-processing.",
        )
        logger.error("Video processing failed for %s: %s", key, exc)
        return ""
    finally:
        # Clean the LOCAL working copy only — the S3 source is retained.
        for p in (local, wav):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _apply_resolved_speakers(segments, resolved: List[dict]) -> None:
    """Overwrite each segment's speaker label with the resolved name (+role) so
    the rendered transcript reads with identities, not 'Speaker N'."""
    by_tc = {r["timecode"]: r for r in resolved}
    for seg in segments:
        r = by_tc.get(seg.timecode)
        if r and r.get("name"):
            role = r.get("role", "")
            suffix = f" ({role})" if role and role != "unknown" else ""
            seg.speaker = f"{r['name']}{suffix}"


def _write_chapter_structure(s3, key, book, markdown, resolved) -> str:
    meta_key = f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    content_key = f"contentrepository/{book}/chapter1/chapter1-content.md"
    n_review = sum(1 for r in resolved if r.get("needs_review"))
    meta = (
        'series: "TODO - Add series name"\n'
        f'book: "{book}"\n'
        'author: "TODO - Add author name"\n'
        'chapter_number: "1"\n'
        'chapter_title: "Video transcript"\n'
        'license: "TODO - Add license"\n'
        'copyright_date: "TODO - Add year"\n'
        f'source_url: "s3://{BUCKET}/{key}"\n'
        f'source_video_key: "{key}"\n'
        f'speaker_id_version: "{SPEAKER_ID_VERSION}"\n'
        f'speakers_needing_review: "{n_review}"\n'
    )
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=meta.encode("utf-8"))
    s3.put_object(Bucket=BUCKET, Key=content_key, Body=markdown.encode("utf-8"))
    logger.info(
        "Video %s -> %s (%d segments, %d speakers need review)",
        key,
        content_key,
        len(resolved),
        n_review,
    )
    return content_key


def _vision_analyzer():
    """Return a VisionAnalyzer (video_perception protocol) backed by Grok vision.
    Reads on-screen captions + ranks roster headshot matches per frame."""
    from src.ingestion.video_vision import GrokVisionAnalyzer

    return GrokVisionAnalyzer()


def _alert(kind: str, message: str) -> None:
    logger.error("ANOMALY [%s]: %s", kind, message)
    topic = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if not topic:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=topic, Subject=f"WWII Pipeline: {kind}", Message=message
        )
    except Exception as e:  # pragma: no cover
        logger.error("Failed to alert [%s]: %s", kind, e)


def main() -> int:
    key = os.getenv("VIDEO_KEY", "")
    if not key:
        logger.error("VIDEO_KEY env var not set — nothing to process")
        return 2
    return 0 if process_video(key) else 1


if __name__ == "__main__":
    sys.exit(main())
